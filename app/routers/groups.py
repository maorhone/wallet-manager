from fastapi import APIRouter, Request

from app.auth.session import CurrentPrincipal, Db
from app.schemas.departments import ModelAmounts
from app.schemas.groups import (
    BulkAllocationIn,
    GroupLimitsIn,
    MemberCapIn,
    MemberDefaultsIn,
    MemberLimitsIn,
)
from app.schemas.platform import GroupAtDepartmentIn, GroupCreateIn, GroupParentIn
from app.services import bulk, groups, members, platform, views
from app.writes import Write, write_response

router = APIRouter()


@router.post("/groups")
async def create_group(body: GroupAtDepartmentIn, ctx: Write, principal: CurrentPrincipal):
    return await write_response(
        ctx, await platform.create_group(ctx, principal, body.department_id, body)
    )


@router.post("/groups/{group_id}/insert-parent")
async def insert_parent(
    group_id: int, body: GroupCreateIn, ctx: Write, principal: CurrentPrincipal
):
    return await write_response(
        ctx, await platform.insert_group_parent(ctx, principal, group_id, body)
    )


@router.patch("/groups/{group_id}/parent")
async def move_parent(group_id: int, body: GroupParentIn, ctx: Write, principal: CurrentPrincipal):
    return await write_response(
        ctx, await platform.move_group(ctx, principal, group_id, body.parent_group_id)
    )


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
    entities = await members.set_caps(
        ctx, principal, req.app.state.directory, group_id, body,
        req.app.state.litellm_reader, req.app.state.clock,
    )  # fmt: skip
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


@router.post("/groups/{group_id}/bulk-allocation/preview")
async def bulk_allocation_preview(
    group_id: int, body: BulkAllocationIn, principal: CurrentPrincipal, db: Db, req: Request
):
    return await bulk.preview(db, principal, req.app.state.directory, group_id, body)


@router.post("/groups/{group_id}/bulk-allocation")
async def bulk_allocation(
    group_id: int, body: BulkAllocationIn, ctx: Write, principal: CurrentPrincipal, req: Request
):
    entities = await bulk.commit(
        ctx, principal, req.app.state.directory, group_id, body,
        req.app.state.litellm_reader, req.app.state.clock,
    )  # fmt: skip
    return await write_response(ctx, entities)
