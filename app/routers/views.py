from fastapi import APIRouter, Query, Request

from app.auth.session import CurrentPrincipal, Db
from app.services import overview

router = APIRouter()


@router.get("/budget/overview")
async def budget_overview(
    request: Request,
    principal: CurrentPrincipal,
    db: Db,
    scope: str,
    period: str | None = Query(None),
):
    return await overview.overview(db, principal, request.app.state.settings, scope, period)


@router.get("/attention")
async def attention(
    request: Request,
    principal: CurrentPrincipal,
    db: Db,
    scope: str,
    period: str | None = Query(None),
):
    return await overview.attention(db, principal, request.app.state.settings, scope, period)
