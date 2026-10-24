"""Step 5.14: per-model service status from health polling (SoT D-060)."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.scheduler import jobs  # noqa: F401  (loop imports jobs; keep import order stable)
from app.services.service_health import run_service_health

PROBES = {
    "m-ok": None,
    "m-limited": "RateLimitError: 429 too many requests",
    "m-loaded": "Timeout: upstream overloaded",
    "m-odd": "something else",
}


@pytest.fixture
async def poll(provisioned, litellm, fake_app, fake_clock, prov_settings):
    async def run(probes=PROBES):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
        ) as c:
            assert (await c.post("/_test/model_health", json=probes)).status_code == 200
        return await run_service_health(provisioned, litellm, fake_clock, settings=prov_settings)

    return run


def by_model(body):
    return {m["model"]: m for m in body["models"]}


async def test_health_states_mapped(poll, login_wm):
    assert await poll() == 4
    dani = await login_wm("dani")
    body = (await dani.get("/status/service")).json()
    assert {m: v["state"] for m, v in by_model(body).items()} == {
        "m-ok": "ok",
        "m-limited": "limited",
        "m-loaded": "loaded",
        "m-odd": "unknown",
    }
    assert body["state"] == "unknown"


async def test_all_ok_overall_ok(poll, login_wm):
    await poll({"a": None, "b": None})
    body = (await (await login_wm("dani")).get("/status/service")).json()
    assert body["state"] == "ok"


async def test_stale_shows_unknown(poll, login_wm, fake_clock, prov_settings):
    # The app under test reads the wall clock, so poll "in the past" instead of advancing it.
    age = timedelta(seconds=2 * prov_settings.service_health_interval_s + 1)
    fake_clock.set(datetime.now(UTC) - age)
    await poll({"a": None})
    body = (await (await login_wm("dani")).get("/status/service")).json()
    assert by_model(body)["a"]["state"] == "unknown" and body["state"] == "unknown"


async def test_missing_rows_unknown_never_ok(login_wm):
    body = (await (await login_wm("dani")).get("/status/service")).json()
    assert body["state"] == "unknown" and body["models"] == []


async def test_failed_poll_keeps_old_rows(poll, login_wm, fake_app):
    await poll({"a": None})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as c:
        await c.post("/_test/outage", json={"on": True})
    body = (await (await login_wm("dani")).get("/status/service")).json()
    assert by_model(body)["a"]["state"] == "ok"


async def test_detail_hidden_from_employee(poll, login_wm):
    await poll()
    dani = (await (await login_wm("dani")).get("/status/service")).json()
    assert all("detail" not in m for m in dani["models"])
    for who in ("aiteam", "admin"):
        body = (await (await login_wm(who)).get("/status/service")).json()
        assert by_model(body)["m-limited"]["detail"].startswith("RateLimitError")
