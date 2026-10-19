from decimal import Decimal

from fastapi import FastAPI
from pydantic import BaseModel

from app.errors import ApiError, install_error_handlers, parent_overallocated
from app.schemas.common import Money


def _client():
    import httpx

    app = FastAPI()
    install_error_handlers(app)

    class Body(BaseModel):
        amount: Money

    @app.get("/boom")
    async def boom():
        raise parent_overallocated(620000, 600000, 600000, rows=[{"upn": "a@x", "code": "X"}])

    @app.get("/plain")
    async def plain():
        raise ApiError(422, "LIMIT_OUT_OF_POLICY", "bad", requested=5, details={"field": "f"})

    @app.post("/body")
    async def body(b: Body):
        return b.model_dump(mode="json")

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_envelope_shape_for_api_error():
    async with _client() as c:
        r = await c.get("/boom")
        assert r.status_code == 422
        j = r.json()
        assert j["code"] == "PARENT_OVERALLOCATED"
        assert (j["requested"], j["available"], j["parent_total"]) == (620000, 600000, 600000)
        assert j["details"]["rows"] == [{"upn": "a@x", "code": "X"}]
        assert j["message"] and j["request_id"] == r.headers["X-Request-ID"]
        r = await c.get("/plain")
        assert r.json()["details"] == {"field": "f"}
        assert (await c.get("/nope")).json()["code"] == "NOT_FOUND"


async def test_validation_error_is_422_invalid_request():
    async with _client() as c:
        r = await c.post("/body", json={"amount": "abc"})
        assert r.status_code == 422
        assert r.json()["code"] == "INVALID_REQUEST"
        assert (await c.post("/body", json={"amount": -1})).json()["code"] == "INVALID_REQUEST"
        ok = await c.post("/body", json={"amount": "12.50"})
        assert ok.json() == {"amount": 12.5}
        assert Decimal("1")
