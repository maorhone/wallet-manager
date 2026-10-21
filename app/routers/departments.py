from fastapi import APIRouter, Request
from sqlalchemy import select

from app.auth.session import CurrentPrincipal, Db
from app.models import Department
from app.schemas.departments import DepartmentBudgetIn, DepartmentSplitIn, ModelAmounts
from app.schemas.platform import DepartmentCreateIn, GroupCreateIn
from app.services import departments, platform, views
from app.writes import Write, write_response

router = APIRouter()


@router.post("/departments")
async def create_department(body: DepartmentCreateIn, ctx: Write, principal: CurrentPrincipal):
    entities = await platform.create_department(ctx, principal, body)
    return await write_response(ctx, entities)


@router.post("/departments/{department_id}/groups")
async def create_group(
    department_id: int, body: GroupCreateIn, ctx: Write, principal: CurrentPrincipal
):
    entities = await platform.create_group(ctx, principal, department_id, body)
    return await write_response(ctx, entities)


@router.get("/departments/{department_id}/summary")
async def department_summary(
    department_id: int,
    request: Request,
    principal: CurrentPrincipal,
    db: Db,
    period: str | None = None,
):
    settings = request.app.state.settings
    return await views.department_summary(
        db, request.app.state.directory, principal, settings, department_id, period
    )


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
