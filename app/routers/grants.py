from fastapi import APIRouter, Request

from app.auth.session import CurrentPrincipal, Db
from app.schemas.grants import GrantCloseIn, GrantIn, GrantPatchIn, ShareIn, SharePatchIn
from app.services import grants, shares
from app.writes import Write, write_response

router = APIRouter()


@router.post("/grants")
async def post_grant(body: GrantIn, request: Request, ctx: Write, principal: CurrentPrincipal):
    app = request.app
    entities = await grants.create_grant(ctx, principal, app.state.directory, app.state.clock, body)
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
    app = request.app
    entities = await grants.close_grant(
        ctx, principal, app.state.clock, grant_id, app.state.litellm, app.state.litellm_reader
    )
    return await write_response(ctx, entities)


@router.get("/grants/{grant_id}/shares")
async def list_shares(grant_id: int, request: Request, principal: CurrentPrincipal, db: Db):
    app = request.app
    return await shares.list_shares(
        db, principal, app.state.clock, app.state.directory, app.state.settings, grant_id
    )


@router.post("/grants/{grant_id}/shares")
async def post_shares(
    grant_id: int, body: list[ShareIn], request: Request, ctx: Write, principal: CurrentPrincipal
):
    app = request.app
    entities = await shares.create_shares(
        ctx, principal, app.state.clock, app.state.directory, grant_id, body
    )
    return await write_response(ctx, entities)


@router.patch("/grants/{grant_id}/shares/{share_id}")
async def patch_share(
    grant_id: int, share_id: int, body: SharePatchIn, request: Request, ctx: Write,
    principal: CurrentPrincipal,
):  # fmt: skip
    app = request.app
    entities = await shares.patch_share(
        ctx, principal, app.state.clock, app.state.directory, grant_id, share_id, body
    )
    return await write_response(ctx, entities)


@router.post("/grants/{grant_id}/shares/{share_id}/close")
async def close_share(
    grant_id: int, share_id: int, request: Request, ctx: Write, principal: CurrentPrincipal,
    body: GrantCloseIn | None = None,
):  # fmt: skip
    app = request.app
    entities = await shares.close_share(
        ctx,
        principal,
        app.state.clock,
        grant_id,
        share_id,
        app.state.litellm,
        app.state.litellm_reader,
        app.state.directory,
    )
    return await write_response(ctx, entities)


@router.get("/grants")
async def list_grants(
    request: Request, principal: CurrentPrincipal, db: Db, department: int | None = None,
    group: int | None = None, status: str | None = None, project_ref: str | None = None,
):  # fmt: skip
    clock = request.app.state.clock
    return await grants.list_grants(
        db,
        principal,
        clock,
        department,
        group,
        status,
        project_ref,
        request.app.state.settings.app_timezone,
    )


@router.get("/departments/{department_id}/pmo-pool")
async def pmo_pool(
    department_id: int, request: Request, principal: CurrentPrincipal, db: Db,
    period: str | None = None,
):  # fmt: skip
    app = request.app
    return await grants.pmo_pool(
        db, principal, app.state.settings, app.state.clock, department_id, period
    )


@router.get("/projects/summary")
@router.get("/projects/{project_ref}/summary")
async def project_summary(request: Request, principal: CurrentPrincipal, db: Db, project_ref: str):
    """`?project_ref=` also serves references containing '/', which the path form cannot carry."""
    return await grants.project_summary(
        db, principal, request.app.state.clock, project_ref, request.app.state.settings.app_timezone
    )
