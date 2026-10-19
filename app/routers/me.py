from datetime import date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.auth.session import CurrentPrincipal, Db
from app.errors import invalid_request
from app.models import Department, Employee, Grp, SpendSnapshot
from app.schemas.me import MeOut, NamedRef, RoleOut, ScopeOut
from app.services import views

router = APIRouter()


@router.get("/me/wallet")
async def my_wallet(
    request: Request, principal: CurrentPrincipal, db: Db, period: str | None = None
):
    return await views.me_wallet(
        db, request.app.state.directory, request.app.state.settings, principal, period
    )


@router.get("/me/usage")
async def my_usage(
    request: Request,
    principal: CurrentPrincipal,
    db: Db,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    granularity: str = "day",
):
    """Own daily spend per model, from the day snapshots."""
    if granularity != "day":
        raise invalid_request("granularity must be 'day'")
    today = datetime.now(ZoneInfo(request.app.state.settings.app_timezone)).date()
    try:
        start = date.fromisoformat(from_) if from_ else today.replace(day=1)
        end = date.fromisoformat(to) if to else today
    except ValueError:
        raise invalid_request("from and to must be YYYY-MM-DD") from None
    rows = await db.scalars(
        select(SpendSnapshot)
        .where(
            SpendSnapshot.scope_kind == "employee",
            SpendSnapshot.scope_id == principal.upn,
            func.length(SpendSnapshot.period) == 10,
            SpendSnapshot.period.between(start.isoformat(), end.isoformat()),
            SpendSnapshot.model.is_not(None),
        )
        .order_by(SpendSnapshot.period, SpendSnapshot.model)
    )
    return [{"day": r.period, "model": r.model, "spend": r.spend} for r in rows]


@router.get("/me", response_model=MeOut)
async def me(request: Request, principal: CurrentPrincipal, db: Db):
    settings = request.app.state.settings
    emp = await db.get(Employee, principal.upn)
    group = await db.get(Grp, emp.group_id) if emp and emp.group_id else None
    dept = await db.get(Department, group.department_id) if group else None

    roles = [RoleOut(role="employee", scope_kind="self")]
    scopes = [ScopeOut(kind="self")]
    for b in principal.bindings:
        scope = None
        if b.scope_kind == "group":
            scope = await db.get(Grp, b.scope_id)
        elif b.scope_kind == "department":
            scope = await db.get(Department, b.scope_id)
        name = scope.name if scope else None
        roles.append(
            RoleOut(role=b.role, scope_kind=b.scope_kind, scope_id=b.scope_id, scope_name=name)
        )
        if b.scope_kind == "global":
            scopes.append(ScopeOut(kind="platform", role=b.role))
        else:
            scopes.append(ScopeOut(kind=b.scope_kind, id=b.scope_id, name=name, role=b.role))

    now = datetime.now(ZoneInfo(settings.app_timezone))
    return MeOut(
        upn=principal.upn,
        display_name=(emp.display_name if emp and emp.display_name else principal.display_name),
        group=NamedRef(id=group.id, name=group.name) if group else None,
        department=NamedRef(id=dept.id, name=dept.name) if dept else None,
        roles=roles,
        scopes=scopes,
        timezone=settings.app_timezone,
        current_period=now.strftime("%Y-%m"),
        dev_auth=settings.dev_auth,
    )
