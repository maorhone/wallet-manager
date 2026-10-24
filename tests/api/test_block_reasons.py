"""Step 5.13: failed requests classified into user-facing block reasons (SoT D-060–D-064)."""

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app.services.blocks import classify

DANI = "dani@corp.example"


def err(status, type_="", message="", **kw):
    at = datetime.now(UTC).isoformat()
    return dict(team_id="A1", user_id=DANI, model="m", status_code=status,
                exception_type=type_, message=message, at=at, **kw)  # fmt: skip


@pytest.fixture
async def errors(provisioned, fake_app):
    rows = [
        err(400, "budget_exceeded", "Budget exceeded for team member"),
        err(429, "max_parallel_requests", "Max parallel request limit reached", retry_after=3),
        err(502, "bad_gateway", "upstream failed"),
        err(418, "weird", "teapot"),
    ]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as c:
        assert (await c.post("/_test/errors", json=rows)).status_code == 200


def test_budget_429_classified_credits_exhausted():
    assert (
        classify(429, "BudgetExceededError", "Budget has been exceeded")[0] == "CREDITS_EXHAUSTED"
    )
    assert classify(400, "budget_exceeded", "Budget exceeded for key")[0] == "CREDITS_EXHAUSTED"


def test_parallel_limit_with_positive_balance_is_rate_limit():
    assert classify(429, "max_parallel_requests", "limit reached") == ("RATE_LIMIT", "concurrency")
    assert classify(429, "", "Rate limit: rpm exceeded") == ("RATE_LIMIT", "rpm")
    assert classify(429, "", "tpm limit reached") == ("RATE_LIMIT", "tpm")


def test_5xx_is_service_error_not_budget():
    assert classify(500, "", "boom")[0] == "SERVICE_ERROR"
    assert classify(503, "", "model overloaded")[0] == "MODEL_CONGESTION"
    assert classify(403, "forbidden", "no key")[0] == "ACCESS_DENIED"


def test_unclassified_is_unknown():
    assert classify(429, "", "")[0] == "UNKNOWN"
    assert classify(None, "", "timeout")[0] == "UNKNOWN"


async def test_me_status_lists_last_blocks(login_wm, errors):
    r = await (await login_wm("dani")).get("/me/status")
    assert r.status_code == 200, r.text
    reasons = {b["reason"]: b for b in r.json()["last_blocks"]}
    assert set(reasons) == {"CREDITS_EXHAUSTED", "RATE_LIMIT", "SERVICE_ERROR", "UNKNOWN"}
    assert reasons["RATE_LIMIT"]["sub"] == "concurrency"
    assert reasons["RATE_LIMIT"]["retry_after"] == 3
    assert reasons["RATE_LIMIT"]["action"] == "REQUEST_THROUGHPUT"
    assert "wallet" in r.json() and "limits" in r.json()


async def test_manager_aggregate_scope_only(login_wm, errors):
    params = {
        "scope": "group",
        "id": "1",
        "from": str(date.today() - timedelta(days=1)),
        "to": str(date.today() + timedelta(days=1)),
    }
    r = await (await login_wm("dana")).get("/reports/blocks", params=params)
    assert r.status_code == 200, r.text
    b = r.json()
    assert {x["reason"]: x["count"] for x in b["by_reason"]}["RATE_LIMIT"] == 1
    assert {x["consumer"] for x in b["by_consumer"]} == {DANI}
    assert "message" not in str(b)
    assert (
        await (await login_wm("viewer")).get(
            "/reports/blocks", params={**params, "scope": "global"}
        )
    ).status_code == 403
    assert (await (await login_wm("dani")).get("/reports/blocks", params=params)).status_code in (
        403,
        404,
    )
