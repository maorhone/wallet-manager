import asyncio
from decimal import Decimal

from sqlalchemy import func, select

from app.db import LockKind
from app.errors import ApiError, invalid_request, scope_forbidden
from app.models import Department, DepartmentModelAllowance, Grp, Organization
from app.permissions import Principal, can_edit_dept_budget, can_edit_split
from app.schemas.departments import DepartmentBudgetIn, DepartmentSplitIn, ModelAmounts
from app.services.validation import check_group_tree, check_sum, internal_allocated
from app.writes import WriteContext


async def set_budget(
    ctx: WriteContext, principal: Principal, department_id: int, body: DepartmentBudgetIn
) -> list[dict]:
    """FR-D1: the platform admin sets a department's budget and period."""
    role = can_edit_dept_budget(principal)
    dept = await ctx.db.get(Department, department_id)
    if dept is None:
        raise ApiError(404, "NOT_FOUND", "Department not found")
    await check_envelope(ctx, dept.organization_id, department_id, body.budget)
    await ctx.db.refresh(dept)
    return await apply_budget(ctx, dept, body, role)


async def dept_allocated(db, organization_id: int, exclude: int | None = None) -> Decimal:
    q = select(func.coalesce(func.sum(Department.budget), 0)).where(
        Department.organization_id == organization_id
    )
    if exclude is not None:
        q = q.where(Department.id != exclude)
    return await db.scalar(q)


async def check_envelope(
    ctx: WriteContext, organization_id: int, department_id: int | None, budget: Decimal
) -> None:
    """Every department budget write fits the organization's departments envelope.

    Takes the organization's allocation lock first (shared by all routes and creation)."""
    await ctx.lock(LockKind.PRODUCTION, -organization_id)
    org = await ctx.db.get(Organization, organization_id)
    await ctx.db.refresh(org)
    others = await dept_allocated(ctx.db, organization_id, exclude=department_id)
    if err := check_sum(others + budget, org.departments_envelope):
        raise err


async def apply_budget(ctx: WriteContext, dept: Department, body: DepartmentBudgetIn, role: str):
    # parent -> child: a reduction may not release what the department already committed to its
    # top-level groups, PMO pool, reserve and active internal systems
    await ctx.lock(LockKind.DEPARTMENT, dept.id)
    await ctx.db.refresh(dept)
    top = await ctx.db.scalar(
        select(func.coalesce(func.sum(Grp.base_budget), 0)).where(
            Grp.department_id == dept.id, Grp.parent_group_id.is_(None), Grp.status != "archived"
        )
    )
    internal = await internal_allocated(ctx.db, dept.id)
    if err := check_sum(top + dept.pmo_pool + dept.reserve + internal, body.budget):
        raise err
    before = {"budget": dept.budget, "budget_period": dept.budget_period}
    dept.budget, dept.budget_period = body.budget, body.budget_period
    after = {"budget": dept.budget, "budget_period": dept.budget_period}
    ctx.audit("department", dept.id, before, after, role, department_id=dept.id)
    await ctx.enqueue("department", dept.id, "/organization/update")
    return [{"entity": "department", "id": dept.id, "values": after}]


async def set_split(
    ctx: WriteContext, principal: Principal, department_id: int, body: DepartmentSplitIn
) -> list[dict]:
    """FR-D2: group bases + PMO pool + reserve must fit the department budget."""
    role = await can_edit_split(ctx.db, principal, department_id)
    await ctx.lock(LockKind.DEPARTMENT, department_id)
    dept = await ctx.db.get(Department, department_id)
    await ctx.db.refresh(dept)  # the permission check may have loaded it before the lock
    groups = {
        g.id: g for g in await ctx.db.scalars(select(Grp).where(Grp.department_id == department_id))
    }
    new = {r.group_id: r.base for r in body.groups}
    if len(new) != len(body.groups):
        raise invalid_request("A group is listed twice")
    if foreign := sorted(set(new) - set(groups)):
        raise scope_forbidden(
            "Group is not in this department",
            [{"group_id": i, "code": "SCOPE_FORBIDDEN"} for i in foreign],
        )
    changed = [g for g in groups.values() if g.id in new and new[g.id] != g.base_budget]
    for parent_id in sorted({g.parent_group_id for g in groups.values() if g.parent_group_id}):
        await ctx.lock(LockKind.GROUP, parent_id)  # serialize with member-cap and key writes
    bases = {g.id: new.get(g.id, g.base_budget) for g in groups.values()}
    rows = [{"group_id": g.id, "code": "PARENT_OVERALLOCATED"} for g in changed]
    if err := await check_group_tree(
        ctx.db, dept, groups.values(), bases, body.pmo_pool, body.reserve, rows
    ):
        raise err
    await asyncio.sleep(0)  # yield after the sums are read; tests widen this to prove the lock

    entities = []
    for g in changed:
        before, g.base_budget = {"base_budget": g.base_budget}, new[g.id]
        after = {"base_budget": g.base_budget}
        ctx.audit("group", g.id, before, after, role, group_id=g.id, department_id=dept.id)
        await ctx.enqueue("group", g.id, "/team/update")
        entities.append({"entity": "group", "id": g.id, "values": after})
    before = {"pmo_pool": dept.pmo_pool, "reserve": dept.reserve}
    dept.pmo_pool, dept.reserve = body.pmo_pool, body.reserve
    after = {"pmo_pool": dept.pmo_pool, "reserve": dept.reserve}
    ctx.audit("department", dept.id, before, after, role, department_id=dept.id)
    return [*entities, {"entity": "department", "id": dept.id, "values": after}]


async def set_allowances(
    ctx: WriteContext, principal: Principal, department_id: int, body: ModelAmounts
) -> list[dict]:
    """FR-D3: the department's declared per-model allowance (a WM value; no LiteLLM write, R5)."""
    role = can_edit_dept_budget(principal)  # admin only (PROGRESS question 5)
    if await ctx.db.get(Department, department_id) is None:
        raise ApiError(404, "NOT_FOUND", "Department not found")
    await ctx.lock(LockKind.DEPARTMENT, department_id)
    rows = {
        r.model: r
        for r in await ctx.db.scalars(
            select(DepartmentModelAllowance).where(
                DepartmentModelAllowance.department_id == department_id
            )
        )
    }
    before = {m: r.amount for m, r in rows.items()}
    after = {m: a for m, a in body.items() if a > 0}
    for model, row in rows.items():
        if model not in after:
            await ctx.db.delete(row)
        else:
            row.amount = after[model]
    for model, amount in after.items():
        if model not in rows:
            ctx.db.add(
                DepartmentModelAllowance(department_id=department_id, model=model, amount=amount)
            )
    values = {"model_allowances": after}
    ctx.audit(
        "department", department_id, {"model_allowances": before}, values, role,
        department_id=department_id,
    )  # fmt: skip
    return [{"entity": "department", "id": department_id, "values": values}]
