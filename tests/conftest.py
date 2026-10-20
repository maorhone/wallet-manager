import asyncio
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from helpers import jwt as portal_jwt
from pgembed import PostgresServer
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.db import create_database, to_asyncpg_url
from app.litellm_client import LiteLLMClient
from app.main import close_app, create_app
from app.seed import seed
from fake_litellm import db as fake_db
from fake_litellm.main import create_app as create_fake_app

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


@pytest_asyncio.fixture(scope="session")
async def fake_engine(litellm_url):
    eng = create_async_engine(litellm_url)
    await fake_db.init_db(eng)  # tables + litellm_ro role/grants
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def fake_app(fake_engine):
    await fake_db.reset_db(fake_engine)
    return create_fake_app(fake_engine)


@pytest_asyncio.fixture
async def litellm(fake_app):
    client = LiteLLMClient(
        "http://fake", "FAKE_MASTER_KEY", transport=httpx.ASGITransport(app=fake_app)
    )
    yield client
    await client.aclose()


@pytest_asyncio.fixture(scope="session")
async def ro_engine(fake_engine, litellm_url):
    url = make_url(litellm_url).set(username="litellm_ro", password=None)
    eng = create_async_engine(url)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def seeded(settings):
    """Dev fixture rows loaded into the (freshly truncated) WM database."""
    app = create_app(settings)
    await seed(app.state.session_factory, settings.seed_file)
    yield app.state.session_factory
    await close_app(app)


@pytest_asyncio.fixture
async def make_client(settings, seeded):
    """Build an ASGI client against the seeded DB; overrides patch Settings fields."""
    made = []

    async def _make(**overrides) -> httpx.AsyncClient:
        app = create_app(settings.model_copy(update=overrides))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        made.append((client, app))
        return client

    yield _make
    for client, app in made:
        await client.aclose()
        await close_app(app)


@pytest_asyncio.fixture
async def login(make_client):
    """Client logged in through the dev login as the given persona."""

    async def _login(who: str, **overrides) -> httpx.AsyncClient:
        client = await make_client(dev_auth=True, **overrides)
        r = await client.post("/auth/dev/login", json={"upn": f"{who}@corp.example"})
        assert r.status_code == 200, r.text
        return client

    return _login


@pytest.fixture
def prov_settings(settings) -> Settings:
    """Settings whose JWT issuer matches the fake LiteLLM's."""
    return settings.model_copy(update={"jwt_issuer": "https://adfs.test"})


@pytest_asyncio.fixture
async def login_wm(prov_settings, seeded, litellm, ro_engine):
    """Like ``login`` but the app talks to the fake LiteLLM (client and read-only reader)."""
    from app.litellm_client import LiteLLMReader

    made = []

    async def _login(who: str) -> httpx.AsyncClient:
        app = create_app(
            prov_settings.model_copy(update={"dev_auth": True}),
            litellm=litellm,
            reader=LiteLLMReader(ro_engine),
        )
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        made.append((client, app))
        r = await client.post("/auth/dev/login", json={"upn": f"{who}@corp.example"})
        assert r.status_code == 200, r.text
        return client

    yield _login
    for client, app in made:
        await client.aclose()
        await close_app(app)


@pytest_asyncio.fixture
async def provisioned(seeded, prov_settings, litellm):
    """Seed + Joiner provisioning enqueued and drained into the fake LiteLLM."""
    from app.outbox.worker import drain

    await seed(seeded, prov_settings.seed_file, settings=prov_settings)
    result = await drain(seeded, litellm, prov_settings)
    assert not result.failed and not result.stopped
    return seeded


@pytest.fixture
def put_budget():
    async def _put(client, dept_id=1, budget=1200000, reason="Q3 plan", **headers):
        h = {"X-CSRF-Token": client.cookies["wm_csrf"], **headers}
        if reason is not None:
            h["X-Change-Reason"] = reason
        return await client.put(
            f"/departments/{dept_id}/budget", json={"budget": budget, "budget_period": "1mo"},
            headers=h,
        )  # fmt: skip

    return _put


class Portal:
    """An app with the onboarding endpoints on, plus a client that sends the two ADFS tokens."""

    def __init__(self, app, client, secret: str):
        self.app, self.client, self.secret = app, client, secret

    def headers(self, upn: str, key: str | None = "", **extra) -> dict:
        h = {
            "Authorization": f"Bearer {portal_jwt.portal_token(self.secret)}",
            "X-User-Assertion": portal_jwt.user_token(self.secret, upn),
        }
        if key is not None:
            h["Idempotency-Key"] = key or secrets.token_hex(8)
        return {**h, **extra}

    async def post(self, upn: str, key: str | None = "", **extra) -> httpx.Response:
        return await self.client.post(
            "/onboarding", json={}, headers=self.headers(upn, key, **extra)
        )

    async def get(self, upn: str) -> httpx.Response:
        return await self.client.get("/onboarding", headers=self.headers(upn, None))


@pytest_asyncio.fixture
async def make_portal(prov_settings, seeded, litellm):
    made = []

    async def _make(**overrides) -> Portal:
        secret = secrets.token_urlsafe(32)
        fields = {
            "dev_auth": True, "dev_jwt_secret": secret, "adfs_issuer": portal_jwt.ISSUER,
            "onboarding_audience": portal_jwt.PORTAL_AUD,
            "onboarding_client_ids": [portal_jwt.CLIENT_ID],
            "onboarding_user_audiences": [portal_jwt.USER_AUD],
            "litellm_public_url": "https://litellm.corp.example",
        }  # fmt: skip
        app = create_app(prov_settings.model_copy(update={**fields, **overrides}), litellm=litellm)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        made.append((client, app))
        return Portal(app, client, secret)

    yield _make
    for client, app in made:
        await client.aclose()
        await close_app(app)


@pytest_asyncio.fixture
async def portal(make_portal):
    return await make_portal()


@pytest_asyncio.fixture
async def api(settings):
    app = create_app(settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    await close_app(app)
