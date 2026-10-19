from app.errors import ApiError
from app.models import Department
from app.permissions import Principal, can_edit_dept_budget
from app.schemas.departments import DepartmentBudgetIn
from app.writes import WriteContext


async def set_budget(
    ctx: WriteContext, principal: Principal, department_id: int, body: DepartmentBudgetIn
) -> list[dict]:
    """FR-D1: the platform admin sets a department's budget and period."""
    role = can_edit_dept_budget(principal)
    dept = await ctx.db.get(Department, department_id)
    if dept is None:
        raise ApiError(404, "NOT_FOUND", "Department not found")
    before = {"budget": dept.budget, "budget_period": dept.budget_period}
    dept.budget, dept.budget_period = body.budget, body.budget_period
    after = {"budget": dept.budget, "budget_period": dept.budget_period}
    ctx.audit("department", dept.id, before, after, role, department_id=dept.id)
    await ctx.enqueue("department", dept.id, "/organization/update")
    return [{"entity": "department", "id": dept.id, "values": after}]
