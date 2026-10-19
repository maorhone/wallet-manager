"""Background worker: ``python -m app.worker``. Later steps add the snapshot loops."""

import asyncio

from app.config import Settings
from app.db import make_engine, make_session_factory
from app.litellm_client import LiteLLMClient
from app.outbox.worker import drain

BACKOFF_START, BACKOFF_MAX = 1.0, 60.0


async def outbox_loop(settings: Settings) -> None:
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    client = LiteLLMClient(settings.litellm_base_url, settings.litellm_master_key)
    backoff = 0.0  # global, in memory: keeps per-entity order without extra columns (NFR4)
    try:
        while True:
            result = await drain(sf, client, settings)
            if result.stopped:
                backoff = min(max(backoff * 2, BACKOFF_START), BACKOFF_MAX)
            else:
                backoff = 0.0
            await asyncio.sleep(max(settings.outbox_poll_s, backoff))
    finally:
        await client.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(outbox_loop(Settings()))
