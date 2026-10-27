from fastapi import APIRouter, Request

from app.auth.session import CurrentPrincipal, Db
from app.schemas.initiatives import InitiativeIn
from app.services import initiatives
from app.writes import Write, write_response

router = APIRouter()


@router.post("/groups/{group_id}/initiatives")
async def create(
    group_id: int, body: InitiativeIn, ctx: Write, principal: CurrentPrincipal, request: Request
):
    state = request.app.state
    entities = await initiatives.create(
        ctx, principal, group_id, body, state.litellm_reader, state.clock
    )
    return await write_response(ctx, entities)


@router.get("/groups/{group_id}/initiatives")
async def list_for_group(group_id: int, principal: CurrentPrincipal, db: Db, request: Request):
    return await initiatives.list_for_group(db, principal, group_id, request.app.state.settings)


@router.get("/initiatives")
async def list_mine(principal: CurrentPrincipal, db: Db, request: Request):
    return await initiatives.list_mine(db, principal, request.app.state.settings)


@router.get("/initiatives/{initiative_id}")
async def get(initiative_id: int, principal: CurrentPrincipal, db: Db, request: Request):
    return await initiatives.get(db, principal, initiative_id, request.app.state.settings)
