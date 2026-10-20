"""Grant shares (SPEC §7.3 FR-P3..P6, §7.3.1): create, change, close and list."""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.clock import Clock
from app.db import LockKind
from app.directory import Directory
from app.errors import (
    ApiError,
    invalid_request,
    member_state_conflict,
    scope_forbidden,
    share_exceeds_grant,
    share_expiry_after_grant,
)
from app.models import Employee, Grant, GrantShare, Grp
from app.permissions import ADMIN, HEAD, REP, Principal, can_share, can_view_grants, target_tier
from app.schemas.grants import ShareIn, SharePatchIn
from app.services.attribution import (
    ShareSpan,
    attribute_periods,
    displayed_used,
    member_spend_by_period,
)
from app.services.lifecycle import check_target
from app.services.spend import Spend, month_bounds
from app.services.views import period_ctx
from app.writes import WriteContext, sync_status

ZERO = Decimal(0)


async def _load(ctx: WriteContext, principal: Principal, grant_id: int) -> tuple[Grant, str]:
    g = await ctx.db.get(Grant, grant_id)
    if g is None or g.group_id is None:
        raise ApiError(404, "NOT_FOUND", "Grant not found")
    role = await can_share(ctx.db, principal, g.group_id)
    await ctx.lock(LockKind.GROUP, g.group_id)
    await ctx.db.refresh(g, with_for_update=True)
    return g, role


def _values(s: GrantShare) -> dict:
    return {
        "grant_id": s.grant_id, "upn": s.upn, "amount": s.amount, "status": s.status,
        "expires_at": s.expires_at.isoformat() if s.expires_at else None,
        "used": s.used, "returned": s.returned, "closed_reason": s.closed_reason,
    }  # fmt: skip


def _row_error(err: ApiError, index: int, upn: str) -> ApiError:
    err.details = {**(err.details or {}), "rows": [{"index": index, "upn": upn}]}
    return err


async def _check_row(
    ctx, principal, directory, role, g: Grant, upn: str, expires_at, now
) -> None:  # fmt: skip
    """Per-row checks 3-4 and 6 of PLAN Step 2.2 (self/scope, state, expiry)."""
    tier = await target_tier(ctx.db, directory, upn)
    check_target(role, principal.upn, upn, tier, "share")
    emp = await ctx.db.get(Employee, upn)
    if emp is None or emp.group_id != g.group_id:
        raise scope_forbidden("Target is not a member of the grant's group")
    if emp.status != "active":
        raise member_state_conflict(emp.status)
    if expires_at is not None:
        if expires_at > g.expires_at:
            raise share_expiry_after_grant()
        if expires_at <= now:
            raise invalid_request("expires_at must be in the future")


async def _shared(ctx, grant_id: int) -> Decimal:
    return await ctx.db.scalar(
        select(func.coalesce(func.sum(GrantShare.amount), 0)).where(GrantShare.grant_id == grant_id)
    )


def _check_total(g: Grant, shared: Decimal, new: Decimal) -> None:
    if shared + new > g.amount:
        raise share_exceeds_grant(new, g.amount - shared, g.amount)


async def create_shares(
    ctx: WriteContext, principal: Principal, clock: Clock, directory: Directory,
    grant_id: int, rows: list[ShareIn],
) -> list[dict]:  # fmt: skip
    g, role = await _load(ctx, principal, grant_id)
    now = clock.now()
    if g.status != "active":
        raise invalid_request("The grant is not active", {"status": g.status})
    taken = set(
        await ctx.db.scalars(
            select(GrantShare.upn).where(GrantShare.grant_id == g.id, GrantShare.status == "active")
        )
    )
    for i, row in enumerate(rows):
        try:
            await _check_row(ctx, principal, directory, role, g, row.upn, row.expires_at, now)
            if row.upn in taken:
                raise invalid_request("Already shared", {"reason": "share_exists"})
        except ApiError as e:
            raise _row_error(e, i, row.upn) from None
        taken.add(row.upn)
    _check_total(g, await _shared(ctx, g.id), sum((r.amount for r in rows), ZERO))

    entities, shares = [], []
    for row in rows:
        s = GrantShare(grant_id=g.id, upn=row.upn, amount=row.amount, expires_at=row.expires_at)
        ctx.db.add(s)
        await ctx.db.flush()
        shares.append(s)
        ctx.audit("grant_share", s.id, {}, _values(s), role, group_id=g.group_id,
                  department_id=g.department_id)  # fmt: skip
        entities.append({"entity": "grant_share", "id": s.id, "values": _values(s)})
    for s in shares:
        await ctx.enqueue("employee", s.upn, "/team/member_update")
        entities.append({"entity": "employee", "id": s.upn, "values": {}})
    return entities


async def _own_share(ctx, g: Grant, share_id: int) -> GrantShare:
    s = await ctx.db.get(GrantShare, share_id)
    if s is None or s.grant_id != g.id:
        raise ApiError(404, "NOT_FOUND", "Share not found")
    await ctx.db.refresh(s)
    return s


async def patch_share(
    ctx: WriteContext, principal: Principal, clock: Clock, directory: Directory,
    grant_id: int, share_id: int, body: SharePatchIn,
) -> list[dict]:  # fmt: skip
    g, role = await _load(ctx, principal, grant_id)
    s = await _own_share(ctx, g, share_id)
    if g.status != "active" or s.status != "active":
        raise invalid_request("The share is not active", {"status": s.status})
    await _check_row(ctx, principal, directory, role, g, s.upn, body.expires_at, clock.now())
    before = _values(s)
    if body.amount is not None:
        _check_total(g, await _shared(ctx, g.id) - s.amount, body.amount)
        s.amount = body.amount
    if body.expires_at is not None:
        s.expires_at = body.expires_at
    ctx.audit("grant_share", s.id, before, _values(s), role, group_id=g.group_id,
              department_id=g.department_id)  # fmt: skip
    await ctx.enqueue("employee", s.upn, "/team/member_update")
    return [
        {"entity": "grant_share", "id": s.id, "values": _values(s)},
        {"entity": "employee", "id": s.upn, "values": {}},
    ]


async def close_shares(
    ctx: WriteContext, shares: list[GrantShare], now: datetime,
    reason: Literal["expiry", "move", "manual"], client, reader, role: str = "system",
    team_update: bool = True, member_update: bool = True,
) -> list[int]:  # fmt: skip
    """Close ``shares`` (FR-P6): attribute usage, return the unused part to their grants.

    One routine for the route, grant close, expiry and member move/delete (Step 2.3).
    """
    tz = ZoneInfo(ctx.settings.app_timezone)
    by_upn: dict[str, list[GrantShare]] = defaultdict(list)
    for s in shares:
        if s.status == "active":
            by_upn[s.upn].append(s)
    closed, groups = [], {}
    for upn, mine in by_upn.items():
        emp = await ctx.db.get(Employee, upn)
        rows = (
            await ctx.db.execute(
                select(GrantShare, Grant).join(Grant, Grant.id == GrantShare.grant_id)
                .where(GrantShare.upn == upn)
            )
        ).all()  # fmt: skip
        grants = {g.id: g for _, g in rows}
        group = await ctx.db.get(Grp, grants[mine[0].grant_id].group_id)
        since = min(
            grants[s.grant_id].starts_at for s, _ in rows if s in mine or s.status == "closed"
        )
        spend = await member_spend_by_period(
            ctx.db, client, reader, upn, group.litellm_team_id, since, now, tz
        )
        by_period = {}
        for p in spend:
            start, end = month_bounds(p, tz)
            by_period[p] = [
                ShareSpan(
                    s.id, s.amount, s.expires_at or g.expires_at,
                    s.used if s.status == "closed" else None,
                )
                for s, g in rows
                if g.starts_at < end and (s.expires_at or g.expires_at) > start
                and (s.status == "closed" or s in mine)
            ]  # fmt: skip
        used = attribute_periods(spend, emp.base_cap, by_period)
        for s in mine:
            before = _values(s)
            s.used = used.get(s.id, ZERO)
            s.returned = s.amount - s.used
            s.status, s.closed_reason = "closed", reason
            grants[s.grant_id].returned += s.returned
            g = grants[s.grant_id]
            ctx.audit("grant_share", s.id, before, _values(s), role, group_id=g.group_id,
                      department_id=g.department_id)  # fmt: skip
            groups[g.group_id] = g
            closed.append(s.id)
        if member_update:
            await ctx.enqueue("employee", upn, "/team/member_update")
    if team_update:
        for group_id in groups:
            await ctx.enqueue("group", group_id, "/team/update")
    return closed


async def close_share(
    ctx: WriteContext, principal: Principal, clock: Clock, grant_id: int, share_id: int,
    client, reader,
) -> list[dict]:  # fmt: skip
    g, role = await _load(ctx, principal, grant_id)
    s = await _own_share(ctx, g, share_id)
    if s.status != "active":
        raise invalid_request("The share is already closed", {"status": s.status})
    await close_shares(ctx, [s], clock.now(), "manual", client, reader, role)
    await ctx.db.flush()
    return [
        {"entity": "grant_share", "id": s.id, "values": _values(s)},
        {"entity": "employee", "id": s.upn, "values": {}},
        {"entity": "group", "id": g.group_id, "values": {"grant_id": g.id}},
    ]


async def list_shares(
    db, principal: Principal, clock: Clock, directory: Directory, settings, grant_id: int
) -> list[dict]:  # fmt: skip
    g = await db.get(Grant, grant_id)
    if g is None or g.group_id is None:
        raise ApiError(404, "NOT_FOUND", "Grant not found")
    await can_view_grants(db, principal, g.department_id, g.group_id)
    roles = principal.group_roles(g.group_id, g.department_id)
    manage = next((r for r in (ADMIN, REP, HEAD) if r in roles), None)
    spend = await Spend.load(db, period_ctx(settings, None)["period"])
    shares = list(
        await db.scalars(
            select(GrantShare).where(GrantShare.grant_id == g.id).order_by(GrantShare.id)
        )
    )
    found = await db.scalars(select(Employee).where(Employee.upn.in_({s.upn for s in shares})))
    emps = {e.upn: e for e in found}
    sync = await sync_status(db, "employee", sorted(emps))
    out = []
    for s in shares:
        e = emps[s.upn]
        used = (await displayed_used(db, s.upn, e.base_cap, spend.used("employee", s.upn)))[s.id]
        can = s.status == "active" and g.status == "active" and manage is not None
        if can:
            try:
                check_target(manage, principal.upn, s.upn, await target_tier(db, directory, s.upn))
            except ApiError:
                can = False
        out.append({
            "id": s.id, "upn": s.upn, "display_name": e.display_name, "amount": s.amount,
            "expires_at": s.expires_at, "status": s.status, "used": used,
            "returned": s.returned, "closed_reason": s.closed_reason,
            "sync_status": sync[s.upn], "actions": {"edit": can, "close": can},
        })  # fmt: skip
    return out
