"""D-066/D-075: bulk allocation replaces each chosen member's base cap."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.db import LockKind
from app.directory import Directory
from app.errors import ApiError, invalid_request
from app.models import GrantShare, Grp
from app.permissions import Principal, can_write_members, target_tier
from app.schemas.groups import BulkAllocationIn, MemberCapIn
from app.services import members
from app.services.personal_values import authorize
from app.services.spend import SpendUnavailable, fresh_spend
from app.services.validation import group_allocated
from app.writes import WriteContext


async def _caps(db, principal: Principal, directory: Directory, group_id: int, body) -> list:
    """The (upn, new cap) rows; `all` skips members the actor may not allocate to."""
    await can_write_members(db, principal, group_id)
    if body.all == (body.upns is not None):
        raise invalid_request("Give either upns or all")
    group = await db.get(Grp, group_id)
    current = await members._members(db, group_id)
    upns = list(current) if body.all else list(dict.fromkeys(body.upns))
    extra = set(body.overrides) - set(upns)
    if extra:
        raise invalid_request("Override for a upn outside the selection", {"upns": sorted(extra)})
    rows = []
    for upn in upns:
        if body.all:
            try:
                authorize(principal, current[upn], group, await target_tier(db, directory, upn))
            except ApiError:
                continue
        rows.append(MemberCapIn(upn=upn, base_cap=body.overrides.get(upn, body.amount)))
    return rows


async def preview(
    db, principal: Principal, directory: Directory, group_id: int, body: BulkAllocationIn
) -> dict:
    rows = await _caps(db, principal, directory, group_id, body)
    group = await db.get(Grp, group_id)
    current = await members._members(db, group_id)
    shares = dict(
        (
            await db.execute(
                select(GrantShare.upn, func.sum(GrantShare.amount))
                .where(GrantShare.status == "active", GrantShare.upn.in_(current))
                .group_by(GrantShare.upn)
            )
        ).all()
    )
    out = []
    for r in rows:
        before = current[r.upn].base_cap if r.upn in current else Decimal(0)
        out.append(
            {
                "upn": r.upn,
                "before": before,
                "after": r.base_cap,
                "effective_total": r.base_cap + (shares.get(r.upn) or 0),
            }
        )
    new = {r.upn: r.base_cap for r in rows}
    own = sum((new.get(u, e.base_cap) for u, e in current.items()), Decimal(0))
    own += await group_allocated(db, group) - sum(
        (e.base_cap for e in current.values()), Decimal(0)
    )
    available = group.base_budget * group.overalloc_factor
    return {
        "rows": out,
        "allocated_after": own,
        "base_budget": group.base_budget,
        "remaining_allocatable": available - own,
        "over_allocation_warning": own > group.base_budget,
        "exceeds_limit": own > available,
    }


async def commit(
    ctx: WriteContext,
    principal: Principal,
    directory: Directory,
    group_id: int,
    body,
    reader=None,
    clock=None,
) -> list[dict]:
    rows = await _caps(ctx.db, principal, directory, group_id, body)
    await ctx.lock(LockKind.GROUP, group_id)
    current = await members._members(ctx.db, group_id)
    cuts = [r for r in rows if r.upn in current and r.base_cap < current[r.upn].base_cap]
    if cuts:  # consumed credit is never returned to the pool (D-043/D-069): refuse, no clamping
        zone = ZoneInfo(ctx.settings.app_timezone)
        try:
            now = (clock.now() if clock else datetime.now(zone)).astimezone(zone)
            spend = await fresh_spend(ctx.db, reader, now)
        except SpendUnavailable:
            raise ApiError(
                503, "SPEND_UNAVAILABLE", "Current usage could not be read; try again shortly"
            ) from None
        low = sorted(r.upn for r in cuts if r.base_cap < spend.used("employee", r.upn))
        if low:
            raise invalid_request(
                "A new cap cannot be below what the member already used",
                {"reason": "below_consumed", "upns": low},
            )
    return await members.set_caps(ctx, principal, directory, group_id, rows)
