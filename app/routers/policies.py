from fastapi import APIRouter
from sqlalchemy import select

from app.auth.session import CurrentPrincipal, Db
from app.models import Policy
from app.schemas.platform import PoliciesIn
from app.services import platform
from app.writes import Write, write_response

router = APIRouter()


@router.get("/policies")
async def get_policies(db: Db, _: CurrentPrincipal):
    """Read-only policy map (the UI needs the limit ranges)."""
    rows = await db.scalars(
        select(Policy).where(~Policy.key.like("member_defaults.%")).order_by(Policy.key)
    )
    return {p.key: p.value for p in rows}


@router.put("/policies")
async def put_policies(body: PoliciesIn, ctx: Write, principal: CurrentPrincipal):
    entities = await platform.set_policies(ctx, principal, body)
    return await write_response(ctx, entities)
