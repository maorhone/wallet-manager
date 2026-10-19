"""Start a persistent local PostgreSQL (pgembed) under pgdata/, create DBs, migrate.

Run: uv run python scripts/dev_db.py   (prints .env lines; Ctrl-C stops the server)
"""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

from pgembed import PostgresServer
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import create_database, to_asyncpg_url
from fake_litellm.db import init_db

ROOT = Path(__file__).resolve().parent.parent


async def init_fake_litellm(url: str) -> None:
    """Fake LiteLLM tables plus the read-only `litellm_ro` role with SELECT grants."""
    engine = create_async_engine(url)
    try:
        await init_db(engine)
    finally:
        await engine.dispose()


def main() -> None:
    # cleanup_mode="stop" stops the server on exit but keeps the data directory.
    server = PostgresServer(ROOT / "pgdata", cleanup_mode="stop")
    for db in ("wm", "litellm_fake"):
        asyncio.run(create_database(server.get_uri(), db, if_missing=True))
    wm = to_asyncpg_url(server.get_uri(), database="wm")
    fake = to_asyncpg_url(server.get_uri(), database="litellm_fake")
    asyncio.run(init_fake_litellm(fake))
    env = {**os.environ, "DATABASE_URL": wm}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True
    )
    print(f"DATABASE_URL={wm}\nLITELLM_DATABASE_URL={fake}\nLITELLM_BASE_URL=http://localhost:4102")
    print("# server running; Ctrl-C to stop", file=sys.stderr)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
