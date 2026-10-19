from fastapi import APIRouter, Request

from app import metrics
from app.auth.session import CurrentPrincipal, Db
from app.errors import ApiError
from app.services import overallocation
from app.writes import plain

router = APIRouter()


@router.get("/reports/overallocation")
async def overallocation_report(request: Request, principal: CurrentPrincipal, db: Db):
    """FR-V2: admin sees everything; representatives and viewers see their departments."""
    visible = overallocation.visible_departments(principal)
    if visible is not None and not visible:
        raise ApiError(403, "FORBIDDEN", "Forbidden")
    found = await overallocation.findings(db, request.app.state.litellm_reader)
    metrics.OVERALLOCATION.set(len(found))
    if visible is not None:
        found = [f for f in found if f["department_id"] in visible]
    return plain({"findings": found})
