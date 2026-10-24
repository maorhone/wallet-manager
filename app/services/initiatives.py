"""Internal Usage initiatives (5.6, D-067): a budget carved from a section, spent through keys."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import LockKind
from app.errors import ApiError, invalid_request
from app.models import Employee, Grp, Initiative, ServiceKey
from app.permissions import (
    ADMIN,
    HEAD,
    Principal,
    can_view_group,
    group_roles_of,
)
from app.services.validation import check_sum, group_allocated


async def create(ctx, principal: Principal, group_id: int, body) -> list[dict]:
    """Admin or the section's head creates; the budget must fit the section's allocatable."""
    roles = await group_roles_of(ctx.db, principal, group_id)
    if principal.is_admin:
        role = ADMIN
    elif HEAD in roles:
        role = HEAD
    else:
        raise ApiError(403, "FORBIDDEN", "Your role does not permit this action")
    await ctx.lock(LockKind.GROUP, group_id)
    group = await ctx.db.get(Grp, group_id)
    await ctx.db.refresh(group)
    if group.status == "archived":
        raise invalid_request("Group is archived")
    if await ctx.db.get(Employee, body.owner_upn) is None:
        raise invalid_request("Owner is not a known employee", {"reason": "unknown_owner"})
    if body.period_start and body.period_end and body.period_end < body.period_start:
        raise invalid_request("period_end is before period_start")
    allocated = await group_allocated(ctx.db, group)
    if err := check_sum(allocated + body.budget, group.base_budget, group.overalloc_factor):
        raise err
    initiative = Initiative(
        **body.model_dump(),
        group_id=group_id,
        department_id=group.department_id,
        created_by=principal.upn,
    )
    ctx.db.add(initiative)
    try:
        await ctx.db.flush()
    except IntegrityError:
        raise ApiError(409, "CONFLICT", "An initiative with this name exists") from None
    after = {"name": initiative.name, "budget": initiative.budget, "owner_upn": body.owner_upn}
    ctx.audit(
        "initiative", initiative.id, {}, after, role,
        group_id=group_id, department_id=group.department_id,
    )  # fmt: skip
    return [{"entity": "initiative", "id": initiative.id, "values": after}]


def tech_status(keys: list[ServiceKey]) -> str:
    statuses = {k.status for k in keys}
    if "active" in statuses:
        return "connected"
    return "pending" if "pending" in statuses else "not_connected"


async def _view(db, initiatives: list[Initiative]) -> list[dict]:
    keys = list(
        await db.scalars(
            select(ServiceKey).where(
                ServiceKey.owner_kind == "initiative",
                ServiceKey.owner_id.in_([i.id for i in initiatives]),
            )
        )
    )
    return [
        {
            "id": i.id,
            "name": i.name,
            "owner_upn": i.owner_upn,
            "group_id": i.group_id,
            "period_start": i.period_start,
            "period_end": i.period_end,
            "budget": i.budget,
            "status": i.status,
            "tech_status": tech_status([k for k in keys if k.owner_id == i.id]),
        }
        for i in initiatives
    ]


async def list_for_group(db, principal: Principal, group_id: int) -> dict:
    """Anyone who may view the section sees all its initiatives; an owner sees only theirs."""
    try:
        await can_view_group(db, principal, group_id)
        only_owner = False
    except ApiError:
        only_owner = True
    query = select(Initiative).where(Initiative.group_id == group_id).order_by(Initiative.id)
    if only_owner:
        query = query.where(Initiative.owner_upn == principal.upn)
    items = list(await db.scalars(query))
    if only_owner and not items:
        raise ApiError(404, "NOT_FOUND", "Entity not found")
    return {"items": await _view(db, items)}


async def list_mine(db, principal: Principal) -> dict:
    query = select(Initiative).where(Initiative.owner_upn == principal.upn).order_by(Initiative.id)
    return {"items": await _view(db, list(await db.scalars(query)))}


async def get(db, principal: Principal, initiative_id: int) -> dict:
    initiative = await db.get(Initiative, initiative_id)
    if initiative is None:
        raise ApiError(404, "NOT_FOUND", "Initiative not found")
    if initiative.owner_upn != principal.upn:
        await can_view_group(db, principal, initiative.group_id)
    return (await _view(db, [initiative]))[0]
