"""Load the dev fixture into the WM database: ``uv run python scripts/seed.py [--reset]``."""

import argparse
import asyncio

from app.config import Settings
from app.db import make_engine, make_session_factory
from app.seed import seed


async def main(reset: bool) -> None:
    settings = Settings()
    engine = make_engine(settings.database_url)
    await seed(make_session_factory(engine), settings.seed_file, reset=reset)
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="truncate all tables first")
    asyncio.run(main(parser.parse_args().reset))
