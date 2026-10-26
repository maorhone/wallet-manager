"""Service keys: synchronous secret delivery, transactional audit and fixed deletion targets."""

import json
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from app.db import LockKind
from app.errors import ApiError, invalid_request
from app.litellm_client import LiteLLMError, ServiceAccountKeyGenerate
from app.models import (
    AuditLog,
    Grp,
    Initiative,
    LitellmWrite,
    Policy,
    ProductionSystem,
    ServiceKey,
)
from app.permissions import AI_TEAM, can_manage_service_keys, can_view_service_keys
from app.services.spend import Spend
from app.services.validation import (
    check_limit,
    check_sum,
    group_allocated,
    key_allocated,
    pending_incoming,
    settled_budget,
)
from app.writes import sync_status, write_response

GEN = "/key/service-account/generate"
FIELDS = ("alias", "max_budget", "max_parallel", "rpm_limit", "models", "status", "retired_budget")


def values(key):
    return {field: getattr(key, field) for field in FIELDS}


async def owner_lock(ctx, kind, owner_id):
    if kind == "initiative":  # an initiative's keys use its section's team (5.6)
        initiative = await ctx.db.get(Initiative, owner_id)
        if initiative is None:
            raise ApiError(404, "NOT_FOUND", "Owner not found")
        return await owner_lock(ctx, "group", initiative.group_id)
    model = Grp if kind == "group" else ProductionSystem
    owner = await ctx.db.get(model, owner_id)
    if owner is None:
        raise ApiError(404, "NOT_FOUND", "Owner not found")
    # Org Systems have no department; they lock under the negated organization id (5.4).
    await ctx.lock(
        LockKind.GROUP if kind == "group" else LockKind.PRODUCTION,
        owner_id if kind == "group" else owner.department_id or -owner.organization_id,
    )
    await ctx.db.refresh(owner)
    return owner


async def require_team_applied(db, entity, owner_id):
    # Missing team/new is not proof the owner is provisioned.
    team_status = await db.scalar(
        select(LitellmWrite.status)
        .where(
            LitellmWrite.entity == entity,
            LitellmWrite.entity_id == str(owner_id),
            LitellmWrite.endpoint == "/team/new",
        )
        .order_by(LitellmWrite.id.desc())
        .limit(1)
    )
    if team_status != "applied":
        raise invalid_request(
            "Owner team is not provisioned", {"sync_status": team_status or "pending"}
        )


async def generate(ctx, client, owner, key, role, before, *, rotating=False):
    old_hash = key.litellm_token_hash
    key.status = "pending"
    request = ServiceAccountKeyGenerate(
        team_id=owner.litellm_team_id,
        key_alias=f"wm-sk-{key.id}",
        max_budget=float(key.max_budget),
        max_parallel_requests=key.max_parallel,
        rpm_limit=key.rpm_limit,
        models=key.models,
    )
    ctx.audit(
        "service_key",
        key.id,
        before,
        {**values(key), "status": "active"},
        role,
        group_id=owner.id if key.owner_kind != "system" else None,
        department_id=owner.department_id,
    )
    write_id = await ctx.enqueue("service_key", key.id, GEN, payload=request.payload())
    # A pending key reserves budget. The worker never dispatches this synchronous endpoint.
    await ctx.db.commit()
    try:
        result = await client.key_service_account_generate(request)
    except LiteLLMError:
        await owner_lock(ctx, key.owner_kind, key.owner_id)
        await ctx.db.refresh(key)
        key.status = "active" if rotating else "failed"
        write = await ctx.db.get(LitellmWrite, write_id)
        # Never persist an upstream error body, which could echo a secret.
        write.status, write.last_error = "failed", "Service key generation failed"
        # Keep the audit trail, but free the Idempotency-Key so a retry is not a "replay".
        failed = f"{ctx.request_id}:failed:{write_id}"
        write.idempotency_key = f"{failed}:1"
        await ctx.db.execute(
            update(AuditLog).where(AuditLog.request_id == ctx.request_id).values(request_id=failed)
        )
        await ctx.db.commit()
        raise ApiError(503, "LITELLM_UNAVAILABLE", "Service key generation failed") from None
    await owner_lock(ctx, key.owner_kind, key.owner_id)
    await ctx.db.refresh(key)
    key.litellm_token_hash, key.status = result.token, "active"
    write = await ctx.db.get(LitellmWrite, write_id)
    write.status, write.last_error = "applied", None
    if rotating:
        await ctx.enqueue("service_key", key.id, "/key/delete", payload={"keys": [old_hash]})
    response = await write_response(
        ctx, [{"entity": "service_key", "id": key.id, "values": values(key)}]
    )
    response.body = json.dumps({**json.loads(response.body), "secret": result.key}).encode()
    response.headers["content-length"] = str(len(response.body))
    response.headers["cache-control"] = "no-store"
    return response


async def initiative_committed(db, initiative_id):
    """Retired credentials reserve their full cap, including pending deletion/in-flight use.

    LiteLLM has no atomic block, drain and final-spend read contract. A snapshot cannot
    prove unused credit is safe to release, so each credential generation retains its
    commitment permanently. Failed upstream generation also retains its reservation:
    a timeout can hide successful upstream creation.
    """
    keys = await db.scalars(
        select(ServiceKey).where(
            ServiceKey.owner_kind == "initiative", ServiceKey.owner_id == initiative_id
        )
    )
    return sum(
        (
            k.retired_budget
            + (
                k.max_budget
                if k.status in ("active", "pending", "revoked", "failed")
                else Decimal(0)
            )
            for k in keys
        ),
        Decimal(0),
    )


async def incoming_pending(db, initiative_id) -> Decimal:
    return await pending_incoming(db, "initiative", initiative_id)


async def create(ctx, principal, client, body):
    role = await can_manage_service_keys(ctx.db, principal, body.owner_kind, body.owner_id)
    owner = await owner_lock(ctx, body.owner_kind, body.owner_id)
    if body.owner_kind == "initiative":
        initiative = await ctx.db.get(Initiative, body.owner_id)
        await ctx.db.refresh(initiative)
        if initiative.status != "active":
            raise invalid_request("Initiative is closed", {"status": initiative.status})
        allocated = await initiative_committed(ctx.db, initiative.id)
        budget = initiative.budget - await incoming_pending(ctx.db, initiative.id)
        err = check_sum(allocated + body.max_budget, budget)
        entity = "group"
    elif body.owner_kind == "group":
        allocated = await group_allocated(ctx.db, owner)
        err = check_sum(
            allocated + body.max_budget,
            await settled_budget(ctx.db, owner),
            owner.overalloc_factor,
        )
        entity = "group"
    else:
        allocated = await key_allocated(ctx.db, "system", owner.id)
        err = check_sum(allocated + body.max_budget, owner.budget)
        entity = "production_system"
    if err:
        raise err
    ai_team = principal.is_admin or principal.has(AI_TEAM, "global")
    for field, policy_name in (("max_parallel", "concurrency"), ("rpm_limit", "rpm")):
        policy = await ctx.db.get(Policy, policy_name)
        if policy and not ai_team:
            # D-065: only the AI team picks technical limits; others get the approved floor.
            setattr(body, field, policy.value["min"])
        if policy and (
            err := check_limit(field, getattr(body, field), policy.value, principal.is_admin)
        ):
            raise err
    await require_team_applied(ctx.db, entity, owner.id)
    key = ServiceKey(**body.model_dump(), status="pending", created_by=principal.upn)
    ctx.db.add(key)
    await ctx.db.flush()
    return await generate(ctx, client, owner, key, role, {})


async def change(ctx, principal, client, key_id, action):
    key = await ctx.db.get(ServiceKey, key_id)
    if key is None:
        raise ApiError(404, "NOT_FOUND", "Service key not found")
    role = await can_manage_service_keys(ctx.db, principal, key.owner_kind, key.owner_id)
    seen = (key.owner_kind, key.owner_id)
    owner = await owner_lock(ctx, *seen)
    await ctx.db.refresh(key)
    if (key.owner_kind, key.owner_id) != seen:  # moved to an Org System meanwhile (5.4)
        raise invalid_request("Service key changed owner; retry", {"reason": "owner_changed"})
    if key.status != "active":
        raise invalid_request("Service key is not active", {"status": key.status})
    before = values(key)
    if action == "rotate":
        if key.owner_kind == "initiative":
            initiative = await ctx.db.get(Initiative, key.owner_id)
            allocated = await initiative_committed(ctx.db, initiative.id)
            budget = initiative.budget - await incoming_pending(ctx.db, initiative.id)
            err = check_sum(allocated + key.max_budget, budget)
            if err:
                raise err
            # Both credentials can spend until deletion has applied. Never refund the
            # old cap based on a stale spend snapshot, even after revocation/deletion.
            key.retired_budget += key.max_budget
        elif key.owner_kind == "system":
            # Another full-cap credential: the whole system commitment must still fit (r3 B3).
            allocated = await key_allocated(ctx.db, "system", owner.id)
            if err := check_sum(allocated + key.max_budget, owner.budget):
                raise err
            key.retired_budget += key.max_budget  # generations merge in spend: keep reserving
        return await generate(ctx, client, owner, key, role, before, rotating=True)
    key.status = "revoked"
    ctx.audit(
        "service_key",
        key.id,
        before,
        values(key),
        role,
        group_id=owner.id if key.owner_kind != "system" else None,
        department_id=owner.department_id,
    )
    await ctx.enqueue(
        "service_key", key.id, "/key/delete", payload={"keys": [key.litellm_token_hash]}
    )
    return await write_response(
        ctx, [{"entity": "service_key", "id": key.id, "values": values(key)}]
    )


async def list_keys(db, principal, settings, clock, owner_kind, owner_id):
    viewed = await can_view_service_keys(db, principal, owner_kind, owner_id)
    manage = (
        principal.is_admin
        or (owner_kind == "group" and principal.has("group_head", "group", owner_id))
        or (owner_kind == "initiative" and viewed == "group_head")
        or (owner_kind == "system" and viewed == "org_allocator")
    )
    keys = list(
        await db.scalars(
            select(ServiceKey)
            .where(
                ServiceKey.owner_kind == owner_kind,
                ServiceKey.owner_id == owner_id,
            )
            .order_by(ServiceKey.id)
        )
    )
    statuses = await sync_status(db, "service_key", [str(k.id) for k in keys])
    period = clock.now().astimezone(ZoneInfo(settings.app_timezone)).strftime("%Y-%m")
    spend = await Spend.load(db, period)
    return {
        "items": [
            {
                "id": k.id,
                "owner_kind": k.owner_kind,
                "owner_id": k.owner_id,
                **values(k),
                "used": spend.used("service_key", k.id),
                "sync_status": statuses[str(k.id)],
                "created_at": k.created_at,
                "actions": {
                    "rotate": manage and k.status == "active",
                    "revoke": manage and k.status == "active",
                },
            }
            for k in keys
        ],
        "spend_taken_at": spend.taken_at,
    }
