from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request

from app.auth.session import CurrentPrincipal, Db
from app.models import Department, Employee, Grp
from app.schemas.me import MeOut, NamedRef, RoleOut, ScopeOut

router = APIRouter()


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
