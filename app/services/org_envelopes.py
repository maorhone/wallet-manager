"""Organization allocation (5.22, WF-03): two separate envelopes, departments and Org Systems."""

from decimal import Decimal

from sqlalchemy import select

from app.config import Settings
from app.db import LockKind
from app.errors import ApiError
from app.models import Department, Initiative, Organization, ProductionSystem
from app.permissions import Principal, can_manage_org_systems, can_manage_platform
from app.schemas.departments import DepartmentBudgetIn
from app.schemas.org_systems import EnvelopesIn
from app.schemas.production import ProductionSystemPatch
from app.services import departments, views
from app.services.overview import _previous, initiative_used
from app.services.production import spend_for_budget_change, system_committed
from app.services.spend import Spend
from app.services.validation import check_sum
from app.writes import WriteContext


async def _org(db, organization_id: int) -> Organization:
    org = await db.get(Organization, organization_id)
    if org is None:
        raise ApiError(404, "NOT_FOUND", "Organization not found")
    return org


async def _systems(db, organization_id: int) -> list[ProductionSystem]:
    return list(
        await db.scalars(
            select(ProductionSystem).where(
                ProductionSystem.organization_id == organization_id,
                ProductionSystem.funding == "organization",
                ProductionSystem.status == "active",
            )
        )
    )


async def get_envelopes(db, principal, settings: Settings, organization_id: int) -> dict:
    can_manage_org_systems(principal, organization_id)
    org = await _org(db, organization_id)
    period = views.period_ctx(settings, None)["period"]
    spend = await Spend.load(db, _previous(period))
    current = await Spend.load(db, period)
    depts = list(
        await db.scalars(select(Department).where(Department.organization_id == organization_id))
    )
    systems = await _systems(db, organization_id)
    # Credential-move sources: every active initiative of the organization, whoever owns it.
    initiatives = list(
        await db.scalars(
            select(Initiative)
            .where(
                Initiative.status == "active",
                Initiative.department_id.in_([d.id for d in depts]),
            )
            .order_by(Initiative.id)
        )
    )
    initiative_rows = [
        {"id": i.id, "name": i.name, "budget": i.budget, "owner_upn": i.owner_upn,
         "used": await initiative_used(db, current, i.id, period, settings.app_timezone)}
        for i in initiatives
    ]  # fmt: skip
    return {
        "organization": {"id": org.id, "name": org.name, "budget_period": org.budget_period},
        "initiatives": initiative_rows,
        "departments": {
            "envelope": org.departments_envelope,
            "allocated": sum((d.budget for d in depts), Decimal(0)),
            "rows": [
                {"id": d.id, "name": d.name, "budget": d.budget,
                 "budget_period": d.budget_period, "previous_used": spend.used("department", d.id)}
                for d in depts
            ],
        },
        "systems": {
            "envelope": org.systems_envelope,
            "allocated": sum((s.budget for s in systems), Decimal(0)),
            "rows": [
                {"id": s.id, "name": s.name, "budget": s.budget, "owner_upn": s.owner_upn,
                 "used": current.used("system", s.id)}
                for s in systems
            ],
        },
    }  # fmt: skip


async def set_envelopes(
    ctx: WriteContext, principal: Principal, organization_id: int, body: EnvelopesIn
) -> list[dict]:
    """The platform admin sizes both envelopes; neither may drop below what is allocated."""
    role = can_manage_platform(principal)
    await ctx.lock(LockKind.PRODUCTION, -organization_id)
    org = await _org(ctx.db, organization_id)
    await ctx.db.refresh(org)
    systems = sum((s.budget for s in await _systems(ctx.db, organization_id)), Decimal(0))
    if err := check_sum(
        await departments.dept_allocated(ctx.db, organization_id), body.departments_envelope
    ):
        raise err
    if err := check_sum(systems, body.systems_envelope):
        raise err
    before = {
        "departments_envelope": org.departments_envelope,
        "systems_envelope": org.systems_envelope,
    }
    org.departments_envelope, org.systems_envelope = (
        body.departments_envelope,
        body.systems_envelope,
    )
    ctx.audit("organization", org.id, before, body.model_dump(), role)
    if org.systems_litellm_org_id:
        await ctx.enqueue("systems_org", org.id, "/organization/update")
    return [{"entity": "organization", "id": org.id, "values": body.model_dump()}]


async def set_department_budget(
    ctx: WriteContext,
    principal: Principal,
    organization_id: int,
    department_id: int,
    body: DepartmentBudgetIn,
) -> list[dict]:
    """Allocator (or admin) allocates to a department within the departments envelope."""
    role = can_manage_org_systems(principal, organization_id)
    await ctx.lock(LockKind.PRODUCTION, -organization_id)
    await _org(ctx.db, organization_id)
    dept = await ctx.db.get(Department, department_id)
    if dept is None or dept.organization_id != organization_id:
        raise ApiError(404, "NOT_FOUND", "Department not found")
    await departments.check_envelope(ctx, organization_id, department_id, body.budget)
    return await departments.apply_budget(ctx, dept, body, role)


async def set_org_system_budget(
    ctx: WriteContext,
    principal: Principal,
    organization_id: int,
    system_id: int,
    body: ProductionSystemPatch,
    reader=None,
) -> list[dict]:
    """Allocate to an Org System within the systems envelope, never below its issued keys."""
    role = can_manage_org_systems(principal, organization_id)
    await ctx.lock(LockKind.PRODUCTION, -organization_id)
    org = await _org(ctx.db, organization_id)
    await ctx.db.refresh(org)
    system = await ctx.db.get(ProductionSystem, system_id)
    if (
        system is None
        or system.funding != "organization"
        or system.organization_id != organization_id
        or system.status != "active"
    ):
        raise ApiError(404, "NOT_FOUND", "Org System not found")
    await ctx.db.refresh(system)
    others = sum(
        (s.budget for s in await _systems(ctx.db, organization_id) if s.id != system_id), Decimal(0)
    )
    if err := check_sum(others + body.budget, org.systems_envelope):
        raise err
    pc = views.period_ctx(ctx.settings, None, None)
    spend = await spend_for_budget_change(ctx, reader, None, system.budget, body.budget, pc)
    if err := check_sum(await system_committed(ctx.db, spend, system), body.budget):
        raise err
    before, system.budget = {"budget": system.budget}, body.budget
    after = {"budget": system.budget}
    ctx.audit("production_system", system.id, before, after, role)
    await ctx.enqueue("production_system", system.id, "/team/update")
    return [{"entity": "production_system", "id": system.id, "values": after}]
