from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request

from app import metrics
from app.auth.session import CurrentPrincipal, Db
from app.errors import ApiError
from app.schemas.reports import (
    AllocationLevel,
    AllocationReport,
    BlockedReport,
    GrantsReport,
    PeaksReport,
    ReportScopeKind,
    UsageReport,
)
from app.services import overallocation, reports
from app.writes import plain

router = APIRouter()


@router.get("/reports/usage", response_model=UsageReport)
async def usage_report(
    request: Request,
    principal: CurrentPrincipal,
    db: Db,
    scope: ReportScopeKind,
    from_: Annotated[date, Query(alias="from")],
    to: date,
    granularity: Literal["day", "week", "month"] = "day",
    by: Literal["model", "member", "tag"] = "model",
    id: str | None = None,
):
    state = request.app.state
    return await reports.usage(
        db,
        principal,
        state.settings,
        state.clock,
        scope,
        id,
        from_,
        to,
        granularity,
        by,
        state.litellm_reader,
    )


@router.get("/reports/concurrency-peaks", response_model=PeaksReport)
async def peaks_report(
    request: Request,
    principal: CurrentPrincipal,
    db: Db,
    scope: ReportScopeKind,
    from_: Annotated[date, Query(alias="from")],
    to: date,
    id: str | None = None,
):
    state = request.app.state
    return await reports.concurrency_peaks(
        db, principal, state.settings, state.litellm_reader, scope, id, from_, to
    )


@router.get("/reports/blocked", response_model=BlockedReport)
async def blocked_report(
    request: Request,
    principal: CurrentPrincipal,
    db: Db,
    scope: ReportScopeKind,
    from_: Annotated[date, Query(alias="from")],
    to: date,
    id: str | None = None,
):
    state = request.app.state
    return await reports.blocked(db, principal, state.settings, state.clock, scope, id, from_, to)


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
