from fastapi import APIRouter

from app.auth.session import CurrentPrincipal
from app.schemas.departments import DepartmentBudgetIn
from app.services import departments
from app.writes import Write, write_response

router = APIRouter()


@router.put("/departments/{department_id}/budget")
async def put_budget(
    department_id: int, body: DepartmentBudgetIn, ctx: Write, principal: CurrentPrincipal
):
    entities = await departments.set_budget(ctx, principal, department_id, body)
    return await write_response(ctx, entities)
