from fastapi import APIRouter

from app.auth.session import CurrentPrincipal
from app.schemas.departments import ModelAmounts
from app.schemas.groups import GroupLimitsIn
from app.services import groups
from app.writes import Write, write_response

router = APIRouter()


@router.put("/groups/{group_id}/model-pots")
async def put_model_pots(
    group_id: int, body: ModelAmounts, ctx: Write, principal: CurrentPrincipal
):
    return await write_response(ctx, await groups.set_model_pots(ctx, principal, group_id, body))


@router.put("/groups/{group_id}/limits")
async def put_limits(group_id: int, body: GroupLimitsIn, ctx: Write, principal: CurrentPrincipal):
    return await write_response(ctx, await groups.set_limits(ctx, principal, group_id, body))
