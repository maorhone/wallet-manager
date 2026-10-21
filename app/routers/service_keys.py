from typing import Literal

from fastapi import APIRouter, Request

from app.auth.session import CurrentPrincipal, Db
from app.schemas.service_keys import ServiceKeyIn
from app.services import service_keys
from app.writes import Write

router = APIRouter()


@router.post("/service-keys")
async def create(body: ServiceKeyIn, request: Request, ctx: Write, principal: CurrentPrincipal):
    return await service_keys.create(ctx, principal, request.app.state.litellm, body)


@router.post("/service-keys/{key_id}/rotate")
async def rotate(key_id: int, request: Request, ctx: Write, principal: CurrentPrincipal):
    return await service_keys.change(ctx, principal, request.app.state.litellm, key_id, "rotate")


@router.post("/service-keys/{key_id}/revoke")
async def revoke(key_id: int, request: Request, ctx: Write, principal: CurrentPrincipal):
    return await service_keys.change(ctx, principal, request.app.state.litellm, key_id, "revoke")


@router.get("/service-keys")
async def list_keys(
    owner_kind: Literal["group", "system"],
    owner_id: int,
    request: Request,
    principal: CurrentPrincipal,
    db: Db,
):
    state = request.app.state
    return await service_keys.list_keys(
        db, principal, state.settings, state.clock, owner_kind, owner_id
    )
