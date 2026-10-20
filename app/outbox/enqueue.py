from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import LitellmWrite
from app.outbox.desired import DesiredUnavailable, build


async def enqueue(
    session: AsyncSession, settings: Settings, entity: str, entity_id: str, endpoint: str,
    idempotency_key: str, *, payload: dict | None = None, depends_on: int | None = None,
) -> int:  # fmt: skip
    """Add a pending outbox row and return its id.

    Without ``payload`` the stored payload is a preview and the worker rebuilds it when applying.
    With ``payload`` it is stored as given (the worker applies it unchanged for frozen endpoints).
    ``depends_on`` holds the row back until that row is applied (SPEC §9.3).
    """
    await session.flush()  # builders read the domain rows written earlier in this transaction
    if payload is None:
        try:
            payload = (await build(session, settings, endpoint, entity_id)).payload()
        except DesiredUnavailable:
            payload = None  # e.g. the JWT mapping needs the key hash, which exists only after apply
    row = LitellmWrite(
        entity=entity, entity_id=entity_id, endpoint=endpoint, payload=payload,
        idempotency_key=idempotency_key, depends_on=depends_on,
    )  # fmt: skip
    session.add(row)
    await session.flush()
    return row.id
