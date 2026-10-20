from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.auth.session import CurrentPrincipal, Db
from app.errors import invalid_request
from app.permissions import can_view_pending
from app.services import onboarding
from app.writes import Write, write_response

router = APIRouter()


class PlaceIn(BaseModel):
    group_id: int


@router.get("/departments/{department_id}/pending-placements")
async def department_pending(department_id: int, principal: CurrentPrincipal, db: Db):
    await can_view_pending(db, principal, department_id)
    return await onboarding.pending_list(db, department_id)


@router.get("/pending-placements")
async def all_pending(principal: CurrentPrincipal, db: Db, department: str | None = None):
    await can_view_pending(db, principal, None)
    if department is None:
        return await onboarding.pending_list(db)
    if department == "none":
        return await onboarding.pending_list(db, None)
    if not department.isdigit():
        raise invalid_request("department must be an id or 'none'")
    return await onboarding.pending_list(db, int(department))


@router.post("/pending-placements/{upn}/place")
async def place(upn: str, body: PlaceIn, ctx: Write, principal: CurrentPrincipal, req: Request):
    entities = await onboarding.place(ctx, principal, req.app.state.directory, upn, body.group_id)
    return await write_response(ctx, entities)
