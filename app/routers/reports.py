from fastapi import APIRouter, Request

from app import metrics
from app.auth.session import CurrentPrincipal, Db
from app.errors import ApiError
from app.schemas.reports import AllocationLevel, AllocationReport, GrantsReport
from app.services import overallocation, reports
from app.writes import plain

router = APIRouter()


@router.get("/reports/allocation", response_model=AllocationReport)
async def allocation_report(
    request: Request,
    principal: CurrentPrincipal,
    db: Db,
    level: AllocationLevel,
    id: str,
    period: str | None = None,
):
    state = request.app.state
    return await reports.allocation(db, principal, state.settings, state.clock, level, id, period)


@router.get("/reports/grants", response_model=GrantsReport)
async def grants_report(
    request: Request,
    principal: CurrentPrincipal,
    db: Db,
    department: int | None = None,
    project_ref: str | None = None,
):
    state = request.app.state
    return await reports.grants_report(
        db, principal, state.settings, state.clock, department, project_ref
    )


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
