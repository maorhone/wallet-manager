"""Production organizations and systems (SPEC §4, DR4, FR-K1, FR-K2; §12.5.4 tab 6)."""

import asyncio
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import Clock
from app.config import Settings
from app.db import LockKind
from app.errors import ApiError, invalid_request
from app.models import Department, Employee, Grp, ProductionSystem, ServiceKey
from app.permissions import (
    ADMIN,
    HEAD,
    Principal,
    can_manage_platform,
    can_view_production,
    group_roles_of,
)
from app.schemas.production import ProductionOrgIn, ProductionSystemIn, ProductionSystemPatch
from app.services.spend import Spend, SpendUnavailable, fresh_spend
from app.services.validation import check_sum, internal_allocated, pending_incoming
from app.services.views import period_ctx
from app.writes import WriteContext, sync_status

ZERO = Decimal(0)


async def _check_department_envelope(
    db, dept: Department, internal: Decimal, budget: Decimal | None = None
) -> None:
    top = await db.scalar(
        select(func.coalesce(func.sum(Grp.base_budget), 0)).where(
            Grp.department_id == dept.id,
            Grp.parent_group_id.is_(None),
            Grp.status != "archived",
        )
    )
    if err := check_sum(
        top + dept.pmo_pool + dept.reserve + internal, dept.budget if budget is None else budget
    ):
        raise err


async def _sync_production_budget(ctx, dept, total):
    # Compatibility storage and the proxy organization follow the funded child teams.
    dept.prod_budget = total
    await ctx.enqueue("production_org", dept.id, "/organization/update")


async def set_production_org(
    ctx: WriteContext, principal: Principal, department_id: int, body: ProductionOrgIn
) -> list[dict]:
    """Legacy setup and period updates; the budget is derived from funded systems."""
    role = can_manage_platform(principal)
    await ctx.lock(LockKind.DEPARTMENT, department_id)
    await ctx.lock(LockKind.PRODUCTION, department_id)
    dept = await ctx.db.get(Department, department_id)
    if dept is None:
        raise ApiError(404, "NOT_FOUND", "Department not found")
    await ctx.db.refresh(dept)
    allocated = await internal_allocated(ctx.db, department_id)
    before = {"budget": dept.prod_budget, "budget_period": dept.prod_budget_period}
    first = dept.prod_litellm_org_id is None
    if first:
        dept.prod_litellm_org_id = f"{dept.litellm_org_id}-prod"
    # An omitted period keeps the current one ("1mo" on first set).
    dept.prod_budget = allocated
    dept.prod_budget_period = body.budget_period or dept.prod_budget_period or "1mo"
    after = {
        "litellm_org_id": dept.prod_litellm_org_id, "budget": dept.prod_budget,
        "budget_period": dept.prod_budget_period,
    }  # fmt: skip
    ctx.audit("production_org", department_id, before, after, role, department_id=department_id)
    endpoint = "/organization/new" if first else "/organization/update"
    await ctx.enqueue("production_org", department_id, endpoint)
    return [{"entity": "production_org", "id": department_id, "values": after}]


async def _check_owner(db, upn: str | None) -> None:
    """D-048: the owner is an existing, active employee (the one who may file targeted requests)."""
    if upn is not None:
        owner = await db.get(Employee, upn)
        if owner is None or owner.status == "left":
            raise invalid_request("Owner is not an active employee", {"field": "owner_upn"})


async def create_production_system(
    ctx: WriteContext, principal: Principal, department_id: int, body: ProductionSystemIn
) -> list[dict]:
    """FR-K2: a Team under the production org (DR4: never the department's own org)."""
    role = can_manage_platform(principal)
    await ctx.lock(LockKind.DEPARTMENT, department_id)
    await ctx.lock(LockKind.PRODUCTION, department_id)
    dept = await ctx.db.get(Department, department_id)
    if dept is None:
        raise ApiError(404, "NOT_FOUND", "Department not found")
    await ctx.db.refresh(dept)
    if dept.prod_litellm_org_id is None:
        raise invalid_request(
            "Department has no production organization", {"reason": "no_production_org"}
        )
    existing = await internal_allocated(ctx.db, department_id)
    total = existing + body.budget
    await _check_department_envelope(ctx.db, dept, total)
    if body.managing_group_id is not None:
        managing = await ctx.db.get(Grp, body.managing_group_id)
        if managing is None or managing.department_id != department_id:
            raise invalid_request("Managing group is not in this department")
    await asyncio.sleep(0)  # yield after the sums are read; tests widen this to prove the lock

    await _check_owner(ctx.db, body.owner_upn)
    system = ProductionSystem(
        department_id=department_id, name=body.name, budget=body.budget, status="active",
        managing_group_id=body.managing_group_id, owner_upn=body.owner_upn,
    )  # fmt: skip
    ctx.db.add(system)
    await ctx.db.flush()
    system.litellm_team_id = f"sys-{system.id}"
    after = {
        "name": system.name,
        "litellm_team_id": system.litellm_team_id,
        "budget": system.budget,
    }
    ctx.audit("production_system", system.id, {}, after, role, department_id=department_id)
    await _sync_production_budget(ctx, dept, total)
    await ctx.enqueue("production_system", system.id, "/team/new")
    return [{"entity": "production_system", "id": system.id, "values": after}]


async def spend_for_budget_change(ctx, reader, clock, current, wanted, pc) -> Spend:
    """A reduction releases funding, so it must see real consumption, not the 5-minute cache."""
    if wanted >= current:
        return await Spend.load(ctx.db, pc["period"])
    now = (clock.now() if clock else datetime.now(ZoneInfo(ctx.settings.app_timezone))).astimezone(
        ZoneInfo(ctx.settings.app_timezone)
    )
    try:
        return await fresh_spend(ctx.db, reader, now)
    except SpendUnavailable:
        raise ApiError(
            503, "SPEND_UNAVAILABLE", "Current usage could not be read; try again shortly"
        ) from None


async def system_committed(db, spend: Spend, system: ProductionSystem) -> Decimal:
    """All consumption of the system (every key it ever had) plus what its keys can still spend.

    Spend is aggregated by system, so rotated and revoked keys are inside ``used("system")``.
    A key that was ever rotated reserves its full cap (and the retired generations theirs):
    per-key spend merges all generations, so it cannot lower the current credential's reserve.
    """
    # Every status: a revoked key's upstream delete may be pending and a failed generation may
    # hide a created credential, so their reservations (and retired generations) stay committed.
    keys = await db.scalars(
        select(ServiceKey).where(
            ServiceKey.owner_kind == "system", ServiceKey.owner_id == system.id
        )
    )
    reserved = ZERO
    for key in keys:
        cap = key.max_budget or ZERO
        if key.retired_budget > ZERO:
            reserved += cap + key.retired_budget
        else:
            reserved += max(cap - spend.used("service_key", key.id), ZERO)
    return (
        spend.used("system", system.id) + reserved + await pending_incoming(db, "system", system.id)
    )


async def update_system_budget(
    ctx: WriteContext,
    principal: Principal,
    system_id: int,
    body: ProductionSystemPatch,
    clock: Clock | None = None,
    reader=None,
) -> list[dict]:
    """Admin, or the head of the system's managing section (delegation), edits the budget."""
    system = await ctx.db.get(ProductionSystem, system_id)
    if system is None or system.funding != "department":
        raise ApiError(404, "NOT_FOUND", "System not found")
    department_id = system.department_id
    await ctx.lock(LockKind.DEPARTMENT, department_id)
    await ctx.lock(LockKind.PRODUCTION, department_id)
    await ctx.db.refresh(system)
    if principal.is_admin:
        role = ADMIN
    elif system.managing_group_id is not None and HEAD in await group_roles_of(
        ctx.db, principal, system.managing_group_id
    ):
        role = HEAD
    else:
        raise ApiError(403, "FORBIDDEN", "Your role does not permit this action")
    dept = await ctx.db.get(Department, department_id)
    await ctx.db.refresh(dept)
    others = await internal_allocated(ctx.db, department_id, exclude_id=system.id)
    await _check_department_envelope(ctx.db, dept, others + body.budget)
    pc = period_ctx(ctx.settings, None, clock.now() if clock else None)
    spend = await spend_for_budget_change(ctx, reader, clock, system.budget, body.budget, pc)
    committed = await system_committed(ctx.db, spend, system)
    if err := check_sum(committed, body.budget):
        raise err
    before, system.budget = {"budget": system.budget}, body.budget
    if body.owner_upn is not None:
        await _check_owner(ctx.db, body.owner_upn)
        before["owner_upn"], system.owner_upn = system.owner_upn, body.owner_upn
    after = {"budget": system.budget, "owner_upn": system.owner_upn}
    ctx.audit("production_system", system.id, before, after, role, department_id=department_id)
    await _sync_production_budget(ctx, dept, others + body.budget)
    await ctx.enqueue("production_system", system.id, "/team/update")
    return [{"entity": "production_system", "id": system.id, "values": after}]


async def get_production(
    db: AsyncSession,
    settings: Settings,
    clock: Clock,
    principal: Principal,
    department_id: int,
    raw_period: str | None = None,
) -> dict:
    """§11.1 GET /production/departments/{id}: read-only for admin and the department's rep.

    A past ``period`` (§12.1) reads that month's spend and offers no edit actions."""
    await can_view_production(db, principal, department_id)
    dept = await db.get(Department, department_id)
    systems = list(
        await db.scalars(
            select(ProductionSystem)
            .where(
                ProductionSystem.department_id == department_id,
                ProductionSystem.status == "active",
                ProductionSystem.funding == "department",
            )
            .order_by(ProductionSystem.id)
        )
    )
    pc = period_ctx(settings, raw_period, clock.now())
    spend = await Spend.load(db, pc["period"])
    sys_sync = await sync_status(db, "production_system", [str(s.id) for s in systems])
    key_counts = dict(
        (
            await db.execute(
                select(ServiceKey.owner_id, func.count())
                .where(
                    ServiceKey.owner_kind == "system",
                    ServiceKey.owner_id.in_([s.id for s in systems]),
                    ServiceKey.status != "revoked",
                )
                .group_by(ServiceKey.owner_id)
            )
        ).all()
    )
    system_rows = [
        {
            "id": s.id,
            "name": s.name,
            "litellm_team_id": s.litellm_team_id,
            "budget": s.budget,
            "used": spend.used("system", s.id),
            "keys_count": key_counts.get(s.id, 0),
            "sync_status": sys_sync[str(s.id)],
        }  # fmt: skip
        for s in systems
    ]
    org = None
    if dept.prod_litellm_org_id is not None:
        used = sum((r["used"] for r in system_rows), ZERO)
        allocated = sum((s.budget for s in systems), ZERO)
        org_sync = (await sync_status(db, "production_org", [str(department_id)]))[
            str(department_id)
        ]
        org = {
            "litellm_org_id": dept.prod_litellm_org_id, "budget": allocated,
            "budget_period": dept.prod_budget_period, "used": used,
            "over_allocated": False, "sync_status": org_sync,
        }  # fmt: skip
    roles = principal.department_roles(department_id)
    return {
        "department_id": department_id, "org": org, "systems": system_rows,
        "spend_taken_at": spend.taken_at, "read_only": pc["read_only"],
        "actions": {"edit": ADMIN in roles and not pc["read_only"]},
    }  # fmt: skip


async def list_owned_systems(db: AsyncSession, principal: Principal, period: str) -> dict:
    """Systems (organization- or department-funded) whose owner is the caller (D-048/D-063)."""
    query = (
        select(ProductionSystem)
        .where(
            ProductionSystem.owner_upn == principal.upn,
            ProductionSystem.status == "active",
        )
        .order_by(ProductionSystem.id)
    )
    spend = await Spend.load(db, period)  # system-level, so a moved key keeps its history
    return {
        "items": [
            {"id": s.id, "name": s.name, "budget": s.budget, "owner_upn": s.owner_upn,
             "funding": s.funding, "department_id": s.department_id,
             "used": spend.used("system", s.id)}
            for s in await db.scalars(query)
        ]
    }  # fmt: skip
