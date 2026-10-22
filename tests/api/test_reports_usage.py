from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.litellm_client import LiteLLMReader
from app.models import RoleBinding
from app.services.spend import take_snapshot

DANI = "dani@corp.example"


async def usage(client, **params):
    return await client.get(
        "/reports/usage",
        params={
            "scope": "group",
            "id": "1",
            "from": "2026-06-01",
            "to": "2026-06-03",
            "granularity": "day",
            "by": "model",
            **params,
        },
    )


async def test_group_by_model_daily_series_matches_snapshot(login, snapshot):
    await snapshot([("group", 1, None, 99), ("group", 1, "m", "12.3456")], period="2026-06-02")
    await snapshot([("group", 2, "m", 100)], period="2026-06-02")
    r = await usage(await login("dana"))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["series"] == [{"bucket": "2026-06-02", "key": "m", "spend": 12.3456}]
    assert b["cap"] == 250000 and b["taken_at"]
    assert Decimal(str(b["series"][0]["spend"])) == Decimal("12.3456")


async def test_by_member_viewer_403(login, snapshot):
    await snapshot([("employee", DANI, None, 20)], period="2026-06-02")
    for who in ("viewer", "pmo"):
        r = await usage(await login(who), scope="department", by="member")
        assert r.status_code == 403, r.text
    b = (await usage(await login("dana"), by="member")).json()
    assert b["series"] == [{"bucket": "2026-06-02", "key": DANI, "spend": 20}]
    assert (
        await usage(await login("dani"), scope="employee", id=DANI, by="member")
    ).status_code == 200
    assert (await usage(await login("yael"), scope="employee", id=DANI)).status_code == 404
    assert (await usage(await login("rep"), scope="department", id=2)).status_code == 404
    assert (await usage(await login("dani"), scope="global")).status_code == 403
    assert (await usage(await login("viewer"), scope="global")).status_code == 403
    assert (await usage(await login("viewer"), scope="global", by="member")).status_code == 403


async def test_global_viewer_aggregate_only(login, seeded, snapshot):
    async with seeded() as db:
        db.add(
            RoleBinding(
                ad_group_dn="CN=wm-viewer-d1,OU=Groups,DC=corp,DC=example",
                role="viewer",
                scope_kind="global",
            )
        )
        await db.commit()
    await snapshot([("department", 1, "m", 10), ("department", 2, "m", 20)], period="2026-06-02")
    viewer = await login("viewer")
    b = (await usage(viewer, scope="global")).json()
    assert b["series"] == [{"bucket": "2026-06-02", "key": "m", "spend": 30}]
    assert b["cap"] == 1500000
    assert (await usage(viewer, scope="global", by="member")).status_code == 403


async def test_week_and_month_buckets_in_app_timezone(login_wm, provisioned, fake_app, ro_engine):
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as c:
        r = await c.post(
            "/_test/spend/bulk",
            json=[
                dict(team_id="A1", user_id=DANI, model="m", amount=7, at="2026-05-31T22:30:00Z"),
                dict(team_id="A1", user_id=DANI, model="m", amount=3, at="2026-06-07T22:30:00Z"),
            ],
        )
        assert r.status_code == 200, r.text
    await take_snapshot(
        provisioned,
        LiteLLMReader(ro_engine),
        datetime(2026, 6, 15, tzinfo=ZoneInfo("Asia/Jerusalem")),
    )
    client = await login_wm("dana")
    b = (await usage(client, to="2026-06-10", granularity="week")).json()
    assert b["series"] == [
        {"bucket": "2026-06-01", "key": "m", "spend": 7},
        {"bucket": "2026-06-08", "key": "m", "spend": 3},
    ]
    b = (await usage(client, to="2026-06-10", granularity="month")).json()
    assert b["series"] == [{"bucket": "2026-06-01", "key": "m", "spend": 10}]


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-06-04"},
        {"to": "2027-07-06"},
        {"from": "bad"},
        {"scope": "other"},
        {"granularity": "hour"},
        {"by": "other"},
        {"id": "oops"},
    ],
)
async def test_range_validation_422(login, params):
    r = await usage(await login("admin"), **params)
    assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"


async def test_400_days_inclusive_and_empty_series(login):
    client = await login("admin")
    r = await usage(client, **{"from": "2025-01-01", "to": "2026-02-04"})
    assert r.status_code == 200 and r.json()["series"] == []
    assert r.json()["taken_at"] is None
    r = await usage(client, **{"from": "2025-01-01", "to": "2026-02-05"})
    assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"
