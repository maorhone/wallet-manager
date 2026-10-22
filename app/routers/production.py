from fastapi import APIRouter, Request

from app.auth.session import CurrentPrincipal, Db
from app.schemas.production import ProductionOrgIn, ProductionSystemIn
from app.services import production
from app.writes import Write, write_response

router = APIRouter()


@router.put("/production/departments/{department_id}")
async def put_production_org(
    department_id: int, body: ProductionOrgIn, ctx: Write, principal: CurrentPrincipal
):
    entities = await production.set_production_org(ctx, principal, department_id, body)
    return await write_response(ctx, entities)


@router.post("/production/departments/{department_id}/systems")
async def post_production_system(
    department_id: int, body: ProductionSystemIn, ctx: Write, principal: CurrentPrincipal
):
    entities = await production.create_production_system(ctx, principal, department_id, body)
    return await write_response(ctx, entities)


@router.get("/production/departments/{department_id}")
async def get_production(
    department_id: int,
    request: Request,
    principal: CurrentPrincipal,
    db: Db,
    period: str | None = None,
):
    app = request.app
    return await production.get_production(
        db, app.state.settings, app.state.clock, principal, department_id, period
    )
