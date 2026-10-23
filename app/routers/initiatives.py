from fastapi import APIRouter

from app.auth.session import CurrentPrincipal, Db
from app.schemas.initiatives import InitiativeIn
from app.services import initiatives
from app.writes import Write, write_response

router = APIRouter()


@router.post("/groups/{group_id}/initiatives")
async def create(group_id: int, body: InitiativeIn, ctx: Write, principal: CurrentPrincipal):
    return await write_response(ctx, await initiatives.create(ctx, principal, group_id, body))


@router.get("/groups/{group_id}/initiatives")
async def list_for_group(group_id: int, principal: CurrentPrincipal, db: Db):
    return await initiatives.list_for_group(db, principal, group_id)


@router.get("/initiatives/{initiative_id}")
async def get(initiative_id: int, principal: CurrentPrincipal, db: Db):
    return await initiatives.get(db, principal, initiative_id)
