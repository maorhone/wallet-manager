import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from pgembed import PostgresServer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.db import create_database, to_asyncpg_url
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def pg_server():
    with tempfile.TemporaryDirectory() as tmp:
        server = PostgresServer(Path(tmp) / "pg", cleanup_mode="delete")
        for name in ("wm_test", "litellm_test"):
            asyncio.run(create_database(server.get_uri(), name))
        yield server
        server.cleanup()


def _db_url(server: PostgresServer, name: str) -> str:
    return to_asyncpg_url(server.get_uri(), database=name)


@pytest.fixture(scope="session")
def wm_url(pg_server) -> str:
    url = _db_url(pg_server, "wm_test")
    env = {**os.environ, "DATABASE_URL": url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True
    )
    return url


@pytest.fixture(scope="session")
def litellm_url(pg_server) -> str:
    return _db_url(pg_server, "litellm_test")


@pytest.fixture(scope="session")
def settings(wm_url, litellm_url) -> Settings:
    return Settings(_env_file=None, database_url=wm_url, litellm_database_url=litellm_url)


@pytest_asyncio.fixture(scope="session")
async def engine(wm_url):
    eng = create_async_engine(wm_url)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(engine):
    async with engine.begin() as conn:
        names = (
            await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                    "AND tablename <> 'alembic_version'"
                )
            )
        ).scalars()
        tables = ", ".join(f'"{n}"' for n in names)
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield


@pytest_asyncio.fixture
async def api(settings):
    app = create_app(settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    await app.state.engine.dispose()
