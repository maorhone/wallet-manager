import os

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from app.litellm_client import LiteLLMClient, LiteLLMReader

from .helpers import Target

_REAL_ENV = ("LITELLM_BASE_URL", "LITELLM_MASTER_KEY", "LITELLM_DATABASE_URL", "LITELLM_TEST_MODEL")


@pytest_asyncio.fixture(params=["fake", "real"])
async def target(request, fake_app, ro_engine):
    if request.param == "fake":
        transport = httpx.ASGITransport(app=fake_app)
        client = LiteLLMClient("http://fake", "FAKE_MASTER_KEY", transport=transport)
        http = httpx.AsyncClient(transport=transport, base_url="http://fake")
        yield Target(
            "fake", client, LiteLLMReader(ro_engine), http, "test-model", "FAKE_MASTER_KEY"
        )
        await client.aclose()
        await http.aclose()
        return
    if missing := [v for v in _REAL_ENV if not os.environ.get(v)]:
        pytest.skip(f"real target needs {', '.join(missing)}")
    base = os.environ["LITELLM_BASE_URL"]
    master = os.environ["LITELLM_MASTER_KEY"]
    client = LiteLLMClient(base, master)
    http = httpx.AsyncClient(base_url=base, timeout=60)
    engine = create_async_engine(os.environ["LITELLM_DATABASE_URL"])
    yield Target(
        "real", client, LiteLLMReader(engine), http, os.environ["LITELLM_TEST_MODEL"], master
    )
    await client.aclose()
    await http.aclose()
    await engine.dispose()
