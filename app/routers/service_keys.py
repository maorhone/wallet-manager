from typing import Literal

from fastapi import APIRouter, Request

from app.auth.session import CurrentPrincipal, Db
from app.schemas.org_systems import KeyMoveIn
from app.schemas.service_keys import ServiceKeyIn
from app.services import org_systems, service_keys
from app.writes import Write, write_response

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


@router.post("/service-keys/{key_id}/move")
async def move(key_id: int, body: KeyMoveIn, ctx: Write, principal: CurrentPrincipal):
    return await write_response(ctx, await org_systems.move_key(ctx, principal, key_id, body))


@router.get("/service-keys")
async def list_keys(
    owner_kind: Literal["group", "system", "initiative"],
    owner_id: int,
    request: Request,
    principal: CurrentPrincipal,
    db: Db,
):
    state = request.app.state
    return await service_keys.list_keys(
        db, principal, state.settings, state.clock, owner_kind, owner_id
    )
