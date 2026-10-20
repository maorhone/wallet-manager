from fastapi import APIRouter, Request

from app.auth.session import CurrentPrincipal, Db
from app.schemas.grants import GrantCloseIn, GrantIn, GrantPatchIn
from app.services import grants
from app.writes import Write, write_response

router = APIRouter()


@router.post("/grants")
async def post_grant(body: GrantIn, request: Request, ctx: Write, principal: CurrentPrincipal):
    entities = await grants.create_grant(ctx, principal, request.app.state.clock, body)
    return await write_response(ctx, entities)


@router.patch("/grants/{grant_id}")
async def patch_grant(
    grant_id: int, body: GrantPatchIn, request: Request, ctx: Write, principal: CurrentPrincipal
):
    entities = await grants.patch_grant(ctx, principal, request.app.state.clock, grant_id, body)
    return await write_response(ctx, entities)


@router.post("/grants/{grant_id}/close")
async def close_grant(
    grant_id: int, request: Request, ctx: Write, principal: CurrentPrincipal,
    body: GrantCloseIn | None = None,
):  # fmt: skip
    entities = await grants.close_grant(ctx, principal, request.app.state.clock, grant_id)
    return await write_response(ctx, entities)


@router.get("/grants")
async def list_grants(
    request: Request, principal: CurrentPrincipal, db: Db, department: int | None = None,
    group: int | None = None, status: str | None = None, project_ref: str | None = None,
):  # fmt: skip
    clock = request.app.state.clock
    return await grants.list_grants(db, principal, clock, department, group, status, project_ref)


@router.get("/departments/{department_id}/pmo-pool")
async def pmo_pool(
    department_id: int, request: Request, principal: CurrentPrincipal, db: Db,
    period: str | None = None,
):  # fmt: skip
    app = request.app
    return await grants.pmo_pool(
        db, principal, app.state.settings, app.state.clock, department_id, period
    )


@router.get("/projects/{project_ref}/summary")
async def project_summary(project_ref: str, request: Request, principal: CurrentPrincipal, db: Db):
    return await grants.project_summary(db, principal, request.app.state.clock, project_ref)
