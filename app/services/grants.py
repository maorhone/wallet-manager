"""PMO grants (SPEC §7.3 FR-P1, P2, P6, P7, P8) and the PMO pool (§12.3)."""

import asyncio
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import Clock
from app.config import Settings
from app.db import LockKind
from app.errors import (
    ApiError,
    grant_below_shared,
    invalid_request,
    pmo_pool_insufficient,
    scope_forbidden,
    share_expiry_after_grant,
)
from app.models import Department, Grant, GrantShare, Grp
from app.permissions import (
    ADMIN,
    HEAD,
    PMO,
    REP,
    Principal,
    can_manage_grants,
    can_view_department,
    can_view_grants,
)
from app.schemas.grants import GrantIn, GrantPatchIn
from app.services import shares as shares_service
from app.services import wallet
from app.services.spend import month_bounds
from app.services.views import period_ctx
from app.writes import WriteContext, sync_status

ZERO = Decimal(0)
_LIVE = ("pending", "active")


def _window(settings: Settings, now: datetime, period: str | None = None):
    """UTC [start, end) of ``period`` (default: the month containing ``now``)."""
    tz = ZoneInfo(settings.app_timezone)
    return month_bounds(period or now.astimezone(tz).strftime("%Y-%m"), tz)


async def _reserved(db: AsyncSession, department_id: int, window) -> tuple[Decimal, Decimal]:
    """(Σ effective of pending + active grants, Σ used of grants closed in the window)."""
    live = await db.scalar(
        select(func.coalesce(func.sum(Grant.amount - Grant.returned), 0)).where(
            Grant.department_id == department_id, Grant.status.in_(_LIVE)
        )
    )
    closed_used = await db.scalar(
        select(func.coalesce(func.sum(Grant.used), 0)).where(
            Grant.department_id == department_id, Grant.status == "closed",
            Grant.closed_at >= window[0], Grant.closed_at < window[1],
        )
    )  # fmt: skip
    return live, closed_used


async def pool_available(db: AsyncSession, dept: Department, settings: Settings, now: datetime):
    """§12.3: pool minus pending/active grants minus what closed grants used this period."""
    live, closed_used = await _reserved(db, dept.id, _window(settings, now))
    return dept.pmo_pool - live - closed_used


async def _shares(db: AsyncSession, grant_ids: list[int]) -> dict[int, tuple[Decimal, Decimal]]:
    """grant id -> (Σ amount of all shares, Σ used of all shares)."""
    rows = await db.execute(
        select(GrantShare.grant_id, func.sum(GrantShare.amount), func.sum(GrantShare.used))
        .where(GrantShare.grant_id.in_(grant_ids))
        .group_by(GrantShare.grant_id)
    )
    return {gid: (shared, used) for gid, shared, used in rows.all()}


def _values(g: Grant) -> dict:
    return {
        "status": g.status, "amount": g.amount, "returned": g.returned,
        "expires_at": g.expires_at.isoformat() if g.expires_at else None,
    }  # fmt: skip


async def _team_update(ctx: WriteContext, g: Grant, role: str, entities: list[dict]) -> None:
    await ctx.enqueue("group", g.group_id, "/team/update")
    values = {"grant_id": g.id}
    ctx.audit("group", g.group_id, {}, values, role, group_id=g.group_id,
              department_id=g.department_id)  # fmt: skip
    entities.append({"entity": "group", "id": g.group_id, "values": values})


def _audit_grant(ctx, g: Grant, before: dict, role: str) -> dict:
    after = _values(g)
    ctx.audit("grant", g.id, before, after, role, group_id=g.group_id,
              department_id=g.department_id)  # fmt: skip
    return {"entity": "grant", "id": g.id, "values": after}


async def _manageable(ctx: WriteContext, principal: Principal, grant_id: int) -> tuple[Grant, str]:
    """Load a grant for a write: 404 if invisible, 403 if not PMO/admin, then lock its pool."""
    g = await ctx.db.get(Grant, grant_id)
    if g is None:
        raise ApiError(404, "NOT_FOUND", "Grant not found")
    await can_view_grants(ctx.db, principal, g.department_id, g.group_id)
    role = can_manage_grants(principal, g.department_id)
    await ctx.lock(LockKind.PMO, g.department_id)
    await ctx.lock(LockKind.GROUP, g.group_id)  # shares of this grant are written under it
    await ctx.db.refresh(g, with_for_update=True)
    return g, role


async def create_grant(
    ctx: WriteContext, principal: Principal, clock: Clock, body: GrantIn
) -> list[dict]:
    group = await ctx.db.get(Grp, body.group_id)
    if group is None:
        raise ApiError(404, "NOT_FOUND", "Group not found")
    roles = principal.department_roles(group.department_id)
    if not roles & {ADMIN, PMO} and any(b.role == PMO for b in principal.bindings):
        raise scope_forbidden("Grants never cross departments (FR-P8)")
    role = can_manage_grants(principal, group.department_id)
    await ctx.lock(LockKind.PMO, group.department_id)
    dept = await ctx.db.get(Department, group.department_id)
    await ctx.db.refresh(dept)
    now = clock.now()
    if group.status != "active":
        raise invalid_request("The group is not active", {"status": group.status})
    if body.expires_at <= max(body.starts_at, now):
        raise invalid_request("expires_at must be after the start and in the future")
    available = await pool_available(ctx.db, dept, ctx.settings, now)
    if body.amount > available:
        raise pmo_pool_insufficient(body.amount, available, dept.pmo_pool)
    await asyncio.sleep(0)  # yield after the sums are read; tests widen this to prove the lock

    active = body.starts_at <= now
    g = Grant(
        department_id=dept.id, group_id=group.id, amount=body.amount, model=body.model,
        project_ref=body.project_ref, purpose=body.purpose, starts_at=body.starts_at,
        expires_at=body.expires_at, status="active" if active else "pending",
        created_by=principal.upn,
    )  # fmt: skip
    ctx.db.add(g)
    await ctx.db.flush()
    entities = [_audit_grant(ctx, g, {}, role)]
    if active:
        await _team_update(ctx, g, role, entities)
    return entities


async def patch_grant(
    ctx: WriteContext, principal: Principal, clock: Clock, grant_id: int, body: GrantPatchIn
) -> list[dict]:
    """FR-P7: raise the amount (pool permitting) or move the expiry; never below the shares."""
    g, role = await _manageable(ctx, principal, grant_id)
    if g.status == "closed":
        raise invalid_request("The grant is closed", {"status": g.status})
    now = clock.now()
    before = _values(g)
    shared = (await _shares(ctx.db, [g.id])).get(g.id, (ZERO, ZERO))[0]
    if body.amount is not None and body.amount != g.amount:
        if body.amount < shared:
            raise grant_below_shared(body.amount, shared, body.amount)
        dept = await ctx.db.get(Department, g.department_id)
        await ctx.db.refresh(dept)
        available = await pool_available(ctx.db, dept, ctx.settings, now)
        if body.amount - g.amount > available:
            raise pmo_pool_insufficient(body.amount - g.amount, available, dept.pmo_pool)
        g.amount = body.amount
    if body.expires_at is not None:
        if body.expires_at <= max(g.starts_at, now):
            raise invalid_request("expires_at must be after the start and in the future")
        latest = await ctx.db.scalar(
            select(func.max(GrantShare.expires_at)).where(
                GrantShare.grant_id == g.id, GrantShare.status == "active"
            )
        )
        if latest is not None and body.expires_at < latest:
            raise share_expiry_after_grant()
        g.expires_at = body.expires_at
    entities = [_audit_grant(ctx, g, before, role)]
    if g.status == "active":
        await _team_update(ctx, g, role, entities)
    return entities


async def _close_grant(
    ctx: WriteContext, g: Grant, now: datetime, role: str, share_reason: str, client, reader,
    member_update: bool = True,
):  # fmt: skip
    """Close ``g`` and return the unused money to the pool (FR-P6); returns the response entities.

    ``share_reason`` is the reason recorded on shares closed with the grant.
    """
    before, was_active = _values(g), g.status == "active"
    open_shares = list(
        await ctx.db.scalars(
            select(GrantShare).where(GrantShare.grant_id == g.id, GrantShare.status == "active")
        )
    )
    await shares_service.close_shares(
        ctx, open_shares, now, share_reason, client, reader, role, team_update=False,
        member_update=member_update,
    )  # fmt: skip
    await ctx.db.flush()
    used = (await _shares(ctx.db, [g.id])).get(g.id, (ZERO, ZERO))[1]
    g.status, g.closed_at, g.used, g.returned = "closed", now, used, g.amount - used
    entities = [_audit_grant(ctx, g, before, role)]
    if was_active:
        await _team_update(ctx, g, role, entities)
    return entities


async def close_grant(
    ctx: WriteContext, principal: Principal, clock: Clock, grant_id: int, client, reader
) -> list[dict]:
    g, role = await _manageable(ctx, principal, grant_id)
    if g.status == "closed":
        raise invalid_request("The grant is already closed", {"status": g.status})
    return await _close_grant(ctx, g, clock.now(), role, "manual", client, reader)


async def _visible(db: AsyncSession, principal: Principal, grants: list[Grant]) -> list[Grant]:
    groups = {g.id: g for g in await db.scalars(select(Grp))}
    return [
        g for g in grants
        if g.group_id in groups
        and principal.group_roles(g.group_id, groups[g.group_id].department_id)
    ]  # fmt: skip


async def _rows(
    db: AsyncSession, principal: Principal, grants: list[Grant], now: datetime
) -> list[dict]:
    groups = {g.id: g for g in await db.scalars(select(Grp))}
    sums = await _shares(db, [g.id for g in grants])
    sync = await sync_status(db, "group", sorted({str(g.group_id) for g in grants}))
    rows = []
    for g in grants:
        shared, share_used = sums.get(g.id, (ZERO, ZERO))
        roles = principal.group_roles(g.group_id, g.department_id)
        manage, open_ = bool(roles & {ADMIN, PMO}), g.status != "closed"
        rows.append({
            "id": g.id, "department_id": g.department_id, "group_id": g.group_id,
            "group_name": groups[g.group_id].name, "project_ref": g.project_ref,
            "purpose": g.purpose, "amount": g.amount, "model": g.model,
            "starts_at": g.starts_at, "expires_at": g.expires_at, "status": g.status,
            "shared": shared, "unshared": g.amount - shared,
            "used": g.used if g.status == "closed" else share_used, "returned": g.returned,
            "days_left": max(0, (g.expires_at - now).days) if open_ and g.expires_at else None,
            "sync_status": sync[str(g.group_id)],
            "actions": {
                "edit": manage and open_, "close": manage and open_,
                "share": g.status == "active" and bool(roles & {ADMIN, REP, HEAD}),
            },
        })  # fmt: skip
    return rows


async def list_grants(
    db: AsyncSession, principal: Principal, clock: Clock, department: int | None = None,
    group: int | None = None, status: str | None = None, project_ref: str | None = None,
) -> list[dict]:  # fmt: skip
    if group is not None:
        g = await db.get(Grp, group)
        if g is None:
            raise ApiError(404, "NOT_FOUND", "Group not found")
        await can_view_grants(db, principal, g.department_id, group)
    elif department is not None:
        await can_view_grants(db, principal, department)
    q = select(Grant).order_by(Grant.expires_at, Grant.id)
    for column, value in (
        (Grant.department_id, department), (Grant.group_id, group),
        (Grant.status, status), (Grant.project_ref, project_ref),
    ):  # fmt: skip
        if value is not None:
            q = q.where(column == value)
    grants = await _visible(db, principal, list(await db.scalars(q)))
    return await _rows(db, principal, grants, clock.now())


async def pmo_pool(
    db: AsyncSession, principal: Principal, settings: Settings, clock: Clock,
    department_id: int, raw_period: str | None,
) -> dict:  # fmt: skip
    """§12.3 PMO pool card for a department and period."""
    await can_view_department(db, principal, department_id)
    dept = await db.get(Department, department_id)
    period = period_ctx(settings, raw_period)["period"]
    start, end = window = _window(settings, clock.now(), period)
    live, closed_used = await _reserved(db, department_id, window)
    returned = await db.scalar(
        select(func.coalesce(func.sum(Grant.returned), 0)).where(
            Grant.department_id == department_id, Grant.status == "closed",
            Grant.closed_at >= start, Grant.closed_at < end,
        )
    )  # fmt: skip
    share_used = await db.scalar(
        select(func.coalesce(func.sum(GrantShare.used), 0))
        .join(Grant, Grant.id == GrantShare.grant_id)
        .where(Grant.department_id == department_id, Grant.status == "active")
    )
    card = wallet.pmo_wallet(dept.pmo_pool, live, closed_used, closed_used + share_used)
    return {
        "department_id": department_id, "period": period, "pool": card["total"],
        "active_grants": live, "used": card["used"], "returned": returned,
        "available": card["unallocated"],
        "actions": {"create_grant": bool(principal.department_roles(department_id) & {ADMIN, PMO})},
    }  # fmt: skip


async def project_summary(
    db: AsyncSession, principal: Principal, clock: Clock, project_ref: str
) -> dict:
    rows = await list_grants(db, principal, clock, project_ref=project_ref)
    if not rows:
        raise ApiError(404, "NOT_FOUND", "Project not found")
    totals = {k: sum((r[k] for r in rows), ZERO) for k in ("amount", "shared", "used", "returned")}
    return {"project_ref": project_ref, "totals": totals, "grants": rows}
