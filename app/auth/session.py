import time
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.auth.csrf import set_csrf_cookie
from app.errors import ApiError
from app.models import RoleBinding
from app.permissions import Principal, load_bindings

SESSION_MAX_AGE = 8 * 3600


async def start_session(
    request: Request, response: Response, upn: str, name: str, groups: list[str]
) -> None:
    """Create the BFF session. Groups are filtered to DNs present in role_binding (cookie size)."""
    async with request.app.state.session_factory() as db:
        known = set(
            (
                await db.execute(
                    select(RoleBinding.ad_group_dn).where(RoleBinding.ad_group_dn.in_(groups))
                )
            ).scalars()
        )
    request.session.clear()
    request.session.update(
        {
            "upn": upn,
            "name": name,
            "groups": [g for g in dict.fromkeys(groups) if g in known],
            "exp": int(time.time()) + SESSION_MAX_AGE,
        }
    )
    set_csrf_cookie(response, request.app.state.settings.cookie_secure)


async def get_db(request: Request):
    async with request.app.state.session_factory() as session:
        yield session


Db = Annotated[AsyncSession, Depends(get_db)]


async def current_principal(request: Request, db: Db) -> Principal:
    s = request.session
    if not s.get("upn") or s.get("exp", 0) < time.time():
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required")
    bindings = await load_bindings(db, s.get("groups", []))
    return Principal(s["upn"], s.get("name") or s["upn"], bindings)


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
