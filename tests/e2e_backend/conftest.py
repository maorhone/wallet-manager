import asyncio

import httpx
import pytest_asyncio

from app.outbox.worker import drain
from fake_litellm.enforcement import make_test_jwt


class Employee:
    """Sends model requests to the fake LiteLLM with the employee's JWT (the production path)."""

    def __init__(self, http: httpx.AsyncClient, upn: str):
        self.http, self.token = http, make_test_jwt(upn)

    async def chat(self, model: str = "test-model") -> httpx.Response:
        return await self.http.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
        )

    async def chat_n(self, n: int, model: str = "test-model") -> list[httpx.Response]:
        return list(await asyncio.gather(*(self.chat(model) for _ in range(n))))


@pytest_asyncio.fixture
async def employee(fake_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as http:
        yield lambda upn: Employee(http, upn)


@pytest_asyncio.fixture
async def drain_all(provisioned, litellm, prov_settings):
    async def _drain():
        result = await drain(provisioned, litellm, prov_settings)
        assert not result.failed and not result.stopped

    return _drain
