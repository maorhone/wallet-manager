from fastapi import APIRouter, Request

from app.auth.session import CurrentPrincipal, Db
from app.permissions import can_manage_platform
from app.schemas.platform import RoleBindingIn
from app.services import platform
from app.writes import Write, write_response

router = APIRouter()


@router.get("/role-bindings")
async def get_role_bindings(
    request: Request,
    principal: CurrentPrincipal,
    db: Db,
    scope_kind: str,
    scope_id: int | None = None,
):
    can_manage_platform(principal)
    return await platform.list_role_bindings(db, request.app.state.directory, scope_kind, scope_id)


@router.put("/role-bindings")
async def put_role_bindings(body: RoleBindingIn, ctx: Write, principal: CurrentPrincipal):
    entities = await platform.put_role_bindings(ctx, principal, body)
    return await write_response(ctx, entities)
