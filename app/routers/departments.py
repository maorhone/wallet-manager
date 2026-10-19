from fastapi import APIRouter
from sqlalchemy import select

from app.auth.session import CurrentPrincipal, Db
from app.models import Department
from app.schemas.departments import DepartmentBudgetIn, DepartmentSplitIn, ModelAmounts
from app.services import departments
from app.writes import Write, write_response

router = APIRouter()


@router.get("/departments")
async def list_departments(db: Db, principal: CurrentPrincipal):
    rows = await db.scalars(select(Department).order_by(Department.id))
    return [
        {
            "id": d.id,
            "name": d.name,
            "budget": d.budget,
            "budget_period": d.budget_period,
            "pmo_pool": d.pmo_pool,
            "reserve": d.reserve,
        }  # fmt: skip
        for d in rows
        if principal.department_roles(d.id)
    ]


@router.put("/departments/{department_id}/budget")
async def put_budget(
    department_id: int, body: DepartmentBudgetIn, ctx: Write, principal: CurrentPrincipal
):
    entities = await departments.set_budget(ctx, principal, department_id, body)
    return await write_response(ctx, entities)


@router.put("/departments/{department_id}/split")
async def put_split(
    department_id: int, body: DepartmentSplitIn, ctx: Write, principal: CurrentPrincipal
):
    entities = await departments.set_split(ctx, principal, department_id, body)
    return await write_response(ctx, entities)


@router.put("/departments/{department_id}/model-allowances")
async def put_model_allowances(
    department_id: int, body: ModelAmounts, ctx: Write, principal: CurrentPrincipal
):
    entities = await departments.set_allowances(ctx, principal, department_id, body)
    return await write_response(ctx, entities)
