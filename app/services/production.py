"""Production organizations and systems (SPEC §4, DR4, FR-K1, FR-K2; §12.5.4 tab 6)."""

import asyncio
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import Clock
from app.config import Settings
from app.db import LockKind
from app.errors import ApiError, invalid_request
from app.models import Department, Grp, ProductionSystem, ServiceKey
from app.permissions import (
    ADMIN,
    HEAD,
    Principal,
    can_manage_platform,
    can_view_production,
    group_roles_of,
)
from app.schemas.production import ProductionOrgIn, ProductionSystemIn, ProductionSystemPatch
from app.services.spend import Spend
from app.services.validation import check_sum, internal_allocated
from app.services.views import period_ctx
from app.writes import WriteContext, sync_status

ZERO = Decimal(0)


async def set_production_org(
    ctx: WriteContext, principal: Principal, department_id: int, body: ProductionOrgIn
) -> list[dict]:
    """FR-K1: the first call creates the production org; later calls update its budget."""
    role = can_manage_platform(principal)
    await ctx.lock(LockKind.PRODUCTION, department_id)
    dept = await ctx.db.get(Department, department_id)
    if dept is None:
        raise ApiError(404, "NOT_FOUND", "Department not found")
    await ctx.db.refresh(dept)
    allocated = await ctx.db.scalar(
        select(func.coalesce(func.sum(ProductionSystem.budget), 0)).where(
            ProductionSystem.department_id == department_id, ProductionSystem.status == "active"
        )
    )
    if err := check_sum(allocated, body.budget):
        raise err
    before = {"budget": dept.prod_budget, "budget_period": dept.prod_budget_period}
    first = dept.prod_litellm_org_id is None
    if first:
        dept.prod_litellm_org_id = f"{dept.litellm_org_id}-prod"
    # An omitted period keeps the current one ("1mo" on first set).
    dept.prod_budget = body.budget
    dept.prod_budget_period = body.budget_period or dept.prod_budget_period or "1mo"
    after = {
        "litellm_org_id": dept.prod_litellm_org_id, "budget": dept.prod_budget,
        "budget_period": dept.prod_budget_period,
    }  # fmt: skip
    ctx.audit("production_org", department_id, before, after, role, department_id=department_id)
    endpoint = "/organization/new" if first else "/organization/update"
    await ctx.enqueue("production_org", department_id, endpoint)
    return [{"entity": "production_org", "id": department_id, "values": after}]


async def create_production_system(
    ctx: WriteContext, principal: Principal, department_id: int, body: ProductionSystemIn
) -> list[dict]:
    """FR-K2: a Team under the production org (DR4: never the department's own org)."""
    role = can_manage_platform(principal)
    await ctx.lock(LockKind.PRODUCTION, department_id)
    dept = await ctx.db.get(Department, department_id)
    if dept is None:
        raise ApiError(404, "NOT_FOUND", "Department not found")
    await ctx.db.refresh(dept)
    if dept.prod_litellm_org_id is None:
        raise invalid_request(
            "Department has no production organization", {"reason": "no_production_org"}
        )
    existing = await ctx.db.scalar(
        select(func.coalesce(func.sum(ProductionSystem.budget), 0)).where(
            ProductionSystem.department_id == department_id, ProductionSystem.status == "active"
        )
    )
    total = existing + body.budget
    if err := check_sum(total, dept.prod_budget or ZERO):
        raise err
    if body.managing_group_id is not None:
        managing = await ctx.db.get(Grp, body.managing_group_id)
        if managing is None or managing.department_id != department_id:
            raise invalid_request("Managing group is not in this department")
    await asyncio.sleep(0)  # yield after the sums are read; tests widen this to prove the lock

    system = ProductionSystem(
        department_id=department_id, name=body.name, budget=body.budget, status="active",
        managing_group_id=body.managing_group_id,
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
    await ctx.enqueue("production_system", system.id, "/team/new")
    return [{"entity": "production_system", "id": system.id, "values": after}]


async def update_system_budget(
    ctx: WriteContext, principal: Principal, system_id: int, body: ProductionSystemPatch
) -> list[dict]:
    """Admin, or the head of the system's managing section (delegation), edits the budget."""
    system = await ctx.db.get(ProductionSystem, system_id)
    if system is None or system.funding != "department":
        raise ApiError(404, "NOT_FOUND", "System not found")
    department_id = system.department_id
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
    if err := check_sum(others + body.budget, dept.prod_budget or ZERO):
        raise err
    before, system.budget = {"budget": system.budget}, body.budget
    after = {"budget": system.budget}
    ctx.audit("production_system", system.id, before, after, role, department_id=department_id)
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
            "litellm_org_id": dept.prod_litellm_org_id, "budget": dept.prod_budget,
            "budget_period": dept.prod_budget_period, "used": used,
            "over_allocated": allocated > (dept.prod_budget or ZERO), "sync_status": org_sync,
        }  # fmt: skip
    roles = principal.department_roles(department_id)
    return {
        "department_id": department_id, "org": org, "systems": system_rows,
        "spend_taken_at": spend.taken_at, "read_only": pc["read_only"],
        "actions": {"edit": ADMIN in roles and not pc["read_only"]},
    }  # fmt: skip
