"""FR-RC: compare owned fields and queue corrections through the durable outbox."""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, or_, select

from app import metrics
from app.db import LockKind, employee_lock_id, lock_parent
from app.directory import FixtureDirectory
from app.litellm_client import JwtKeyMappingDelete, LiteLLMPermanentError
from app.models import AuditLog, Department, Employee, Grp, LitellmWrite
from app.outbox.desired import DesiredUnavailable, desired_fields
from app.services.notifications import notify, reps_or_admins
from app.writes import WriteContext

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FieldDrift:
    object: str
    id: str
    field: str
    live: object
    desired: object


def diff(object: str, id: str, desired: dict, live: dict) -> list[FieldDrift]:
    out = []

    def compare(path, want, got):
        if path.split(".")[-1] in {"role", "user_role"}:
            return
        if isinstance(want, dict) or isinstance(got, dict):
            for key in sorted(set(want or {}) | set(got or {})):
                compare(f"{path}.{key}", (want or {}).get(key), (got or {}).get(key))
            return
        if want == got:
            return
        if want is not None and got is not None:
            try:
                if (
                    not isinstance(want, bool)
                    and not isinstance(got, bool)
                    and abs(Decimal(str(want)) - Decimal(str(got))) <= Decimal("0.01")
                ):
                    return
            except InvalidOperation:
                pass
            try:
                if datetime.fromisoformat(str(want)).replace(
                    microsecond=0
                ) == datetime.fromisoformat(str(got)).replace(microsecond=0):
                    return
            except ValueError:
                pass
        out.append(FieldDrift(object, id, path, got, want))

    for key in desired:
        compare(key, desired[key], live.get(key))
    return out


@dataclass
class ReconcileResult:
    checked: int = 0
    drifts: list[FieldDrift] = field(default_factory=list)
    corrected: int = 0
    skipped_in_flight: int = 0
    missing: int = 0
    alerts: int = 0


async def run_reconcile(session_factory, client, clock, *, settings) -> ReconcileResult:
    now, result, observations = clock.now(), ReconcileResult(), []
    own_requests = []
    async with session_factory() as db:
        start = await db.scalar(select(func.coalesce(func.max(LitellmWrite.id), 0)))
        departments = list(await db.scalars(select(Department)))
        groups = {g.id: g for g in await db.scalars(select(Grp).where(Grp.status != "archived"))}
        employees = list(
            await db.scalars(select(Employee).where(Employee.status.in_(["active", "blocked"])))
        )

    def outbox_entity(obj):  # the domain names the other writers and lifecycle guards use
        return {"organization": "department", "team": "group"}.get(obj, "employee")

    async def busy(db, obj, id, after=False):
        return (
            await db.scalar(
                select(LitellmWrite.id)
                .where(
                    LitellmWrite.entity == outbox_entity(obj),
                    LitellmWrite.entity_id == id,
                    func.split_part(LitellmWrite.idempotency_key, ":", 1).not_in(own_requests)
                    | LitellmWrite.idempotency_key.is_(None),
                    or_(LitellmWrite.status.in_(["pending", "failed"]), LitellmWrite.id > start)
                    if after
                    else LitellmWrite.status.in_(["pending", "failed"]),
                )
                .limit(1)
            )
            is not None
        )

    targets = [("organization", str(d.id), d) for d in departments]
    targets += [("team", str(g.id), g) for g in groups.values()]
    targets += [
        (obj, e.upn, e)
        for e in employees
        if e.group_id in groups
        for obj in ("employee", "key", "jwt_mapping")
    ]
    groups_of = {e.upn: e.group_id for e in employees}
    # Finish reads before committing any correction: an outage cannot leave a partial scan's writes.
    for obj, id, row in targets:
        async with session_factory() as db:
            if await busy(db, obj, id):
                result.skipped_in_flight += 1
                continue
            if obj == "key" and not row.key_token_hash:
                observations.append((obj, id, {}, None))
                continue
            try:
                desired = await desired_fields(db, settings, obj, id)
            except DesiredUnavailable:  # the employee left or lost the key mid-scan
                result.skipped_in_flight += 1
                continue
        live = None
        try:
            if obj == "organization":
                live = (
                    await client.organization_info(row.litellm_org_id)
                ).litellm_budget_table.model_dump(mode="json")
            elif obj in {"team", "employee"}:
                info = await client.team_info(
                    row.litellm_team_id if obj == "team" else groups[row.group_id].litellm_team_id
                )
                if obj == "team":
                    live = info.team_info.model_dump(mode="json")
                    live.update(
                        {
                            k: live["metadata"].get(k)
                            for k in desired
                            if k.startswith("team_member_")
                        }
                    )
                else:
                    member = next((m for m in info.team_memberships if m.user_id == id), None)
                    if member and member.litellm_budget_table:
                        live = member.litellm_budget_table.model_dump(mode="json")
                        live["max_budget_in_team"] = live.pop("max_budget")
                        live["temp_budget_increase"] = live.get("temp_budget_increase") or 0
            elif obj == "key":
                key = next(
                    (k for k in (await client.key_list(id)).keys if k.token == row.key_token_hash),
                    None,
                )
                if key:
                    live = key.model_dump(mode="json")
            else:
                mapping = next(
                    (
                        m
                        for m in await client.jwt_mapping_list()
                        if m.jwt_issuer == settings.jwt_issuer
                        and m.jwt_claim_name == "upn"
                        and m.jwt_claim_value == id
                    ),
                    None,
                )
                if mapping:
                    live = mapping.model_dump(mode="json")
        except LiteLLMPermanentError as exc:
            if exc.status != 404:
                raise
        if live is not None and "metadata" in desired:
            live["metadata"] = {
                "model_tpm_limit": (live.get("metadata") or {}).get("model_tpm_limit")
            }
        observations.append((obj, id, desired, live))

    for obj, id, desired, live in observations:
        async with session_factory() as db:
            # Same locks and order as the lifecycle actions; held through the commit below.
            if obj == "organization":
                await lock_parent(db, LockKind.DEPARTMENT, int(id))
            elif obj == "team":
                await lock_parent(db, LockKind.GROUP, int(id))
            else:
                emp = await db.get(Employee, id)
                await lock_parent(db, LockKind.EMPLOYEE, employee_lock_id(id))
                if emp.group_id is not None:
                    await lock_parent(db, LockKind.GROUP, emp.group_id)
                await db.refresh(emp)
                if (
                    emp.status not in ("active", "blocked")
                    or emp.group_id != groups_of[id]
                    or (obj == "key" and not emp.key_token_hash)
                ):
                    result.skipped_in_flight += 1
                    continue
            if await busy(db, obj, id, True):
                result.skipped_in_flight += 1
                continue
            result.checked += 1
            if live is None:
                result.missing += 1
                log.warning("Missing reconciliation object %s %s; not recreated", obj, id)
                created = await notify(
                    db,
                    await reps_or_admins(db, FixtureDirectory(settings.seed_file), None),
                    "reconcile_missing",
                    None,
                    None,
                    {"object": obj, "id": id},
                    f"missing:{obj}:{id}:{now.date()}",
                    settings=settings,
                )
                await db.commit()
                if created:
                    result.alerts += 1
                    metrics.RECONCILE_ALERTS.inc()
                continue
            # Re-read desired values after I/O as well as checking new outbox rows.
            desired = await desired_fields(db, settings, obj, id)
            drifts = diff(obj, id, desired, live)
            if not drifts:
                continue
            result.drifts.extend(drifts)
            ctx = WriteContext(
                db, settings, "system:reconcile", str(uuid.uuid4()), "drift correction"
            )
            own_requests.append(ctx.request_id)
            before = {d.field: d.live for d in drifts}
            after = {d.field: d.desired for d in drifts}
            ctx.audit(obj, id, before, after, "reconciliation")
            await db.flush()
            audit = await db.scalar(select(AuditLog).where(AuditLog.request_id == ctx.request_id))
            audit.at = now
            endpoints = {
                "organization": "/organization/update",
                "team": "/team/update",
                "employee": "/team/member_update",
                "jwt_mapping": "/jwt/key/mapping/update",
            }
            entity = outbox_entity(obj)
            if obj == "key":
                if any(d.field != "blocked" for d in drifts):
                    await ctx.enqueue(entity, id, "/key/update")
                if "blocked" in after:
                    await ctx.enqueue(
                        entity, id, "/key/block" if desired["blocked"] else "/key/unblock"
                    )
            elif "token" in after:  # points at another key: re-point it, never mint a new key
                gone = JwtKeyMappingDelete(
                    jwt_issuer=settings.jwt_issuer, jwt_claim_name="upn", jwt_claim_value=id
                )
                await ctx.enqueue(
                    entity, id, "/jwt/key/mapping/delete", payload=gone.payload(), chain=True
                )
                await ctx.enqueue(entity, id, "/jwt/key/mapping/new", chain=True)
                await ctx.enqueue(entity, id, endpoints[obj], chain=True)
            else:
                await ctx.enqueue(entity, id, endpoints[obj])
            for d in drifts:
                metrics.RECONCILE_DRIFT.labels(obj, d.field).inc()
                await db.flush()
                count = await db.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.actor_upn == "system:reconcile",
                        AuditLog.entity == obj,
                        AuditLog.entity_id == id,
                        AuditLog.at >= now - timedelta(hours=settings.reconcile_drift_window_h),
                        AuditLog.at <= now,
                        AuditLog.after.has_key(d.field),
                    )
                )
                if count >= settings.reconcile_drift_alert_count:
                    log.warning("Repeated reconciliation drift %s %s %s", obj, id, d.field)
                    created = await notify(
                        db,
                        await reps_or_admins(db, FixtureDirectory(settings.seed_file), None),
                        "drift_repeated",
                        None,
                        None,
                        {"object": obj, "id": id, "field": d.field},
                        f"drift:{obj}:{id}:{d.field}:{now.date()}",
                        settings=settings,
                    )
                    if created:
                        result.alerts += 1
                        metrics.RECONCILE_ALERTS.inc()
            await db.commit()
            result.corrected += 1
    metrics.RECONCILE_LAST_RUN.set(now.timestamp())
    return result
