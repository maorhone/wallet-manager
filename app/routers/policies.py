from fastapi import APIRouter
from sqlalchemy import select

from app.auth.session import CurrentPrincipal, Db
from app.models import Policy
from app.schemas.platform import PoliciesIn, RateCardIn
from app.services import platform, rate_card
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


@router.get("/rate-card")
async def get_rate_card(db: Db, _: CurrentPrincipal):
    return {"rates": await rate_card.list_rates(db)}


@router.put("/rate-card")
async def put_rate_card(body: RateCardIn, ctx: Write, principal: CurrentPrincipal):
    entities = await rate_card.set_rates(ctx, principal, body)
    return await write_response(ctx, entities)
