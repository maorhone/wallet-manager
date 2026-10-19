from fastapi import APIRouter
from sqlalchemy import select

from app.auth.session import CurrentPrincipal, Db
from app.models import Policy

router = APIRouter()


@router.get("/policies")
async def get_policies(db: Db, _: CurrentPrincipal):
    """Read-only policy map (the UI needs the limit ranges); PUT /policies is Phase 3."""
    rows = await db.scalars(
        select(Policy).where(~Policy.key.like("member_defaults.%")).order_by(Policy.key)
    )
    return {p.key: p.value for p in rows}
