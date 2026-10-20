"""Throw-away full stack for Playwright: ``uv run python scripts/e2e_stack.py``.

pgembed in a temp dir, migrations, fake LiteLLM (E2E_FAKE_PORT, default 4112), seed, API
(E2E_API_PORT, default 4111, DEV_AUTH=1) and the worker. Writes E2E_STATE_FILE (default
frontend/.e2e-state.json: URLs + env) and runs until SIGTERM/SIGINT.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from pgembed import PostgresServer
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import create_database, to_asyncpg_url
from fake_litellm.db import init_db

ROOT = Path(__file__).resolve().parent.parent
# Ports/state file are overridable so scripts/test.sh can run next to a live preview stack.
STATE_FILE = Path(os.environ.get("E2E_STATE_FILE", ROOT / "frontend" / ".e2e-state.json"))
API_PORT = int(os.environ.get("E2E_API_PORT", "4111"))
FAKE_PORT = int(os.environ.get("E2E_FAKE_PORT", "4112"))


async def create_databases(uri: str, fake_url: str) -> None:
    for db in ("wm", "litellm_fake"):
        await create_database(uri, db, if_missing=True)
    engine = create_async_engine(fake_url)
    try:
        await init_db(engine)
    finally:
        await engine.dispose()


def wait_for(url: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise TimeoutError(url)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="wm-e2e-"))
    server = PostgresServer(tmp / "pgdata", cleanup_mode="stop")
    wm = to_asyncpg_url(server.get_uri(), database="wm")
    fake = to_asyncpg_url(server.get_uri(), database="litellm_fake")
    asyncio.run(create_databases(server.get_uri(), fake))
    overrides = {
        "DATABASE_URL": wm,
        "LITELLM_DATABASE_URL": fake,
        "LITELLM_BASE_URL": f"http://127.0.0.1:{FAKE_PORT}",
        "LITELLM_MASTER_KEY": "sk-e2e-master",
        "FAKE_MASTER_KEY": "sk-e2e-master",
        "DEV_AUTH": "1",
        "SPEND_SNAPSHOT_INTERVAL_S": "2",
        "OUTBOX_POLL_S": "0.5",
        "SEED_FILE": str(ROOT / "seed" / "dev.yaml"),
    }
    env = {**os.environ, **overrides}
    state = {
        "api": f"http://127.0.0.1:{API_PORT}",
        "fake": f"http://127.0.0.1:{FAKE_PORT}",
        "env": overrides,
    }
    STATE_FILE.write_text(json.dumps(state, indent=2))
    log = (tmp / "stack.log").open("w")
    procs: list[subprocess.Popen] = []

    def spawn(*args: str) -> None:
        procs.append(
            subprocess.Popen(
                [sys.executable, *args], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
            )
        )

    def interrupt(*_) -> None:
        sys.exit(0)

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True
        )
        spawn("-m", "uvicorn", "fake_litellm.main:app", "--port", str(FAKE_PORT))
        wait_for(f"http://127.0.0.1:{FAKE_PORT}/health/liveliness")
        subprocess.run(
            [sys.executable, "scripts/seed.py", "--reset"], cwd=ROOT, env=env, check=True
        )
        spawn("-m", "uvicorn", "app.main:create_app", "--factory", "--port", str(API_PORT))
        spawn("-m", "app.worker")
        wait_for(f"http://127.0.0.1:{API_PORT}/readyz")
        print(f"e2e stack ready (logs: {tmp / 'stack.log'})", flush=True)
        while True:
            time.sleep(3600)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        server.cleanup()


if __name__ == "__main__":
    main()
