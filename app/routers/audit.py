from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.auth.session import CurrentPrincipal, Db
from app.services import audit

router = APIRouter()


@router.get("/audit")
async def get_audit(
    principal: CurrentPrincipal,
    db: Db,
    entity: str | None = None,
    id: str | None = None,
    actor: str | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
):
    await audit.authorize(db, principal, entity, id)
    return await audit.timeline(db, entity, id, actor, from_, to)
