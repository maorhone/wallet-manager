from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import LitellmWrite
from app.outbox.desired import DesiredUnavailable, build


async def enqueue(
    session: AsyncSession, settings: Settings, entity: str, entity_id: str, endpoint: str,
    idempotency_key: str,
) -> None:  # fmt: skip
    """Add a pending outbox row. The payload is a preview; the worker rebuilds it when applying."""
    await session.flush()  # builders read the domain rows written earlier in this transaction
    try:
        payload = (await build(session, settings, endpoint, entity_id)).payload()
    except DesiredUnavailable:
        payload = None  # e.g. the JWT mapping needs the key hash, which exists only after apply
    session.add(
        LitellmWrite(
            entity=entity, entity_id=entity_id, endpoint=endpoint, payload=payload,
            idempotency_key=idempotency_key,
        )
    )  # fmt: skip
    await session.flush()
