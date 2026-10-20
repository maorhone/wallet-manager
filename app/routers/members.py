from fastapi import APIRouter, Request

from app.auth.session import CurrentPrincipal, Db
from app.schemas.members import DeleteIn, MoveIn
from app.services import lifecycle
from app.writes import Write, write_response

router = APIRouter()
_PATH = "/groups/{group_id}/members/{upn}"


@router.get(_PATH + "/move-targets")
async def move_targets(group_id: int, upn: str, principal: CurrentPrincipal, db: Db):
    return await lifecycle.move_targets(db, principal, group_id)


@router.post(_PATH + "/kick")
async def kick(group_id: int, upn: str, ctx: Write, principal: CurrentPrincipal, req: Request):
    entities = await lifecycle.kick(ctx, principal, req.app.state.directory, group_id, upn)
    return await write_response(ctx, entities)


@router.post(_PATH + "/restore")
async def restore(group_id: int, upn: str, ctx: Write, principal: CurrentPrincipal, req: Request):
    entities = await lifecycle.restore(ctx, principal, req.app.state.directory, group_id, upn)
    return await write_response(ctx, entities)


@router.post(_PATH + "/move")
async def move(
    group_id: int, upn: str, body: MoveIn, ctx: Write, principal: CurrentPrincipal, req: Request
):
    entities = await lifecycle.move(
        ctx, principal, req.app.state.directory, group_id, upn, body.to_group_id
    )
    return await write_response(ctx, entities)


@router.post(_PATH + "/delete")
async def delete(
    group_id: int, upn: str, body: DeleteIn, ctx: Write, principal: CurrentPrincipal, req: Request
):
    entities = await lifecycle.delete_member(
        ctx, principal, req.app.state.directory, group_id, upn, body.confirm_upn
    )
    return await write_response(ctx, entities)
