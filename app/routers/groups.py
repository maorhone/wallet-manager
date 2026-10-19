from fastapi import APIRouter, Request

from app.auth.session import CurrentPrincipal, Db
from app.schemas.departments import ModelAmounts
from app.schemas.groups import GroupLimitsIn, MemberCapIn, MemberDefaultsIn, MemberLimitsIn
from app.services import groups, members, views
from app.writes import Write, write_response

router = APIRouter()


@router.get("/groups/{group_id}/summary")
async def group_summary(
    group_id: int, request: Request, principal: CurrentPrincipal, db: Db, period: str | None = None
):
    settings = request.app.state.settings
    return await views.group_summary(db, principal, settings, group_id, period)


@router.get("/groups/{group_id}/members")
async def list_members(
    group_id: int, request: Request, principal: CurrentPrincipal, db: Db, period: str | None = None
):
    app = request.app
    return await views.members_list(
        db, app.state.directory, app.state.settings, principal, group_id, period
    )


@router.put("/groups/{group_id}/model-pots")
async def put_model_pots(
    group_id: int, body: ModelAmounts, ctx: Write, principal: CurrentPrincipal
):
    return await write_response(ctx, await groups.set_model_pots(ctx, principal, group_id, body))


@router.put("/groups/{group_id}/limits")
async def put_limits(group_id: int, body: GroupLimitsIn, ctx: Write, principal: CurrentPrincipal):
    return await write_response(ctx, await groups.set_limits(ctx, principal, group_id, body))


@router.put("/groups/{group_id}/member-defaults")
async def put_member_defaults(
    group_id: int, body: MemberDefaultsIn, ctx: Write, principal: CurrentPrincipal
):
    return await write_response(ctx, await members.set_defaults(ctx, principal, group_id, body))


@router.put("/groups/{group_id}/members/caps")
async def put_member_caps(
    group_id: int, body: list[MemberCapIn], ctx: Write, principal: CurrentPrincipal, req: Request
):
    entities = await members.set_caps(ctx, principal, req.app.state.directory, group_id, body)
    return await write_response(ctx, entities)


@router.put("/groups/{group_id}/members/{upn}/model-caps")
async def put_member_model_caps(
    group_id: int,
    upn: str,
    body: ModelAmounts,
    ctx: Write,
    principal: CurrentPrincipal,
    req: Request,
):
    entities = await members.set_model_caps(
        ctx, principal, req.app.state.directory, group_id, upn, body
    )
    return await write_response(ctx, entities)


@router.put("/groups/{group_id}/members/{upn}/limits")
async def put_member_limits(
    group_id: int,
    upn: str,
    body: MemberLimitsIn,
    ctx: Write,
    principal: CurrentPrincipal,
    req: Request,
):
    entities = await members.set_limits(
        ctx, principal, req.app.state.directory, group_id, upn, body
    )
    return await write_response(ctx, entities)
