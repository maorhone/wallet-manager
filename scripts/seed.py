"""Load the dev fixture and provision LiteLLM: ``uv run python scripts/seed.py [--reset]``."""

import argparse
import asyncio

import httpx

from app.config import Settings
from app.db import make_engine, make_session_factory
from app.litellm_client import LiteLLMClient
from app.outbox.worker import drain
from app.seed import seed


async def main(reset: bool) -> None:
    settings = Settings()
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    if reset:  # fake LiteLLM only (the dev stack); never aimed at a real proxy
        async with httpx.AsyncClient(base_url=settings.litellm_base_url) as http:
            (await http.post("/_test/reset")).raise_for_status()
    await seed(sf, settings.seed_file, reset=reset, settings=settings)
    client = LiteLLMClient(settings.litellm_base_url, settings.litellm_master_key)
    try:
        result = await drain(sf, client, settings)
    finally:
        await client.aclose()
    print(f"provisioning: {result}")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="truncate all tables first")
    asyncio.run(main(parser.parse_args().reset))
