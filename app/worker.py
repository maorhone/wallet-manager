"""Background worker: ``python -m app.worker``. Outbox and spend-snapshot loops."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.db import make_engine, make_session_factory
from app.litellm_client import LiteLLMClient, LiteLLMReader
from app.outbox.worker import drain
from app.services.spend import take_snapshot

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


async def snapshot_loop(settings: Settings) -> None:
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    ro_engine = create_async_engine(settings.litellm_database_url)
    reader = LiteLLMReader(ro_engine)
    try:
        while True:
            await take_snapshot(sf, reader, datetime.now(ZoneInfo(settings.app_timezone)))
            await asyncio.sleep(settings.spend_snapshot_interval_s)
    finally:
        await ro_engine.dispose()
        await engine.dispose()


async def main(settings: Settings) -> None:
    await asyncio.gather(outbox_loop(settings), snapshot_loop(settings))


if __name__ == "__main__":
    asyncio.run(main(Settings()))
