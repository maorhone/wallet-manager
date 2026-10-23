from fastapi import APIRouter, Request

from app.auth.session import CurrentPrincipal, Db
from app.schemas.org_systems import OrgSystemIn
from app.schemas.production import ProductionOrgIn, ProductionSystemIn, ProductionSystemPatch
from app.services import org_systems, production
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


@router.patch("/production/systems/{system_id}")
async def patch_production_system(
    system_id: int, body: ProductionSystemPatch, ctx: Write, principal: CurrentPrincipal
):
    entities = await production.update_system_budget(ctx, principal, system_id, body)
    return await write_response(ctx, entities)


@router.post("/organizations/{organization_id}/systems")
async def post_org_system(
    organization_id: int, body: OrgSystemIn, ctx: Write, principal: CurrentPrincipal
):
    entities = await org_systems.create_org_system(ctx, principal, organization_id, body)
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
