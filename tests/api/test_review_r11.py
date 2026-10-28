"""Phase 5 round-10 re-review (fixed in round 11): regression tests."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx

from app.models import Grp, Initiative, RateCard, ServiceKey, SpendSnapshot
from app.services.spend import Spend
from tests.api.test_review_r8 import _audit, _tokens
from tests.api.test_review_r10 import PERIOD, YAEL, _quiet_group

TZ = "Asia/Jerusalem"


# review-p5-r10 major B1a1: a key moved to a system keeps counting as the initiative's spend
async def test_r11_group_committed_counts_moved_key_spend(provisioned):
    from tests.helpers.commit import group_committed

    async with provisioned() as s:
        await _quiet_group(s)
        ini = Initiative(
            name="M", owner_upn=YAEL, group_id=1, department_id=1, budget=100, created_by=YAEL
        )
        s.add(ini)
        await s.flush()
        key = ServiceKey(owner_kind="system", owner_id=1, max_budget=1, status="active")
        s.add(key)
        await s.flush()
        await _audit(s, "service_key", key.id, {"owner_kind": "initiative", "owner_id": ini.id},
                     {"owner_kind": "system", "owner_id": 1}, datetime.now(UTC))  # fmt: skip
        for kind, sid in (("service_key", key.id), ("service_key_group", key.id), ("group", 1)):
            s.add(SpendSnapshot(scope_kind=kind, scope_id=str(sid), period=PERIOD, model=None,
                                spend=80))  # fmt: skip
        await s.commit()
        spend = await Spend.load(s, PERIOD)
        assert await group_committed(s, spend, await s.get(Grp, 1), tz=TZ) == 100


# review-p5-r11 major B1a1: a move before the current month still counts inside the window
async def test_r12_group_committed_counts_prior_month_move(provisioned):
    from tests.helpers.commit import group_committed

    async with provisioned() as s:
        await _quiet_group(s)
        ini = Initiative(
            name="M", owner_upn=YAEL, group_id=1, department_id=1, budget=100, created_by=YAEL
        )
        s.add(ini)
        await s.flush()
        key = ServiceKey(owner_kind="system", owner_id=1, max_budget=1, status="active")
        s.add(key)
        await s.flush()
        await _audit(s, "service_key", key.id, {"owner_kind": "initiative", "owner_id": ini.id},
                     {"owner_kind": "system", "owner_id": 1},
                     datetime(2020, 1, 5, tzinfo=UTC))  # fmt: skip
        for kind, sid in (("service_key", key.id), ("service_key_group", key.id), ("group", 1)):
            s.add(SpendSnapshot(scope_kind=kind, scope_id=str(sid), period=PERIOD, model=None,
                                spend=80))  # fmt: skip
        await s.commit()
        spend = await Spend.load(s, PERIOD)
        assert await group_committed(s, spend, await s.get(Grp, 1), tz=TZ) == 100


async def _child(s):
    child = Grp(litellm_team_id="r11-child", department_id=1, name="C", base_budget=0,
                parent_group_id=1)  # fmt: skip
    s.add(child)
    await s.flush()
    return child


# review-p5-r10 major B3: the section's metered value covers its descendants like `used`
async def test_r11_metered_value_includes_descendants(login_wm, provisioned):
    async with provisioned() as s:
        child = await _child(s)
        s.add(RateCard(model="gpt-4", input_per_1m=10, output_per_1m=0))
        await _tokens(s, PERIOD, "gpt-4", 1_000_000, sid=str(child.id))
        await s.commit()
    body = (await (await login_wm("dana")).get("/budget/overview?scope=group:1")).json()
    assert Decimal(str(body["kpis"]["metered_value"])) == 10


# review-p5-r10 major B3: a descendant that cannot be priced makes the value unavailable
async def test_r11_metered_value_unavailable_for_unpriced_descendant(login_wm, provisioned):
    async with provisioned() as s:
        child = await _child(s)
        s.add(RateCard(model="gpt-4", input_per_1m=10, output_per_1m=0))
        await _tokens(s, PERIOD, "gpt-4", 1_000_000)
        await _tokens(s, PERIOD, "unmapped", 5, sid=str(child.id))
        await s.commit()
    body = (await (await login_wm("dana")).get("/budget/overview?scope=group:1")).json()
    assert body["kpis"]["metered_value"] is None


# review-p5-r10 major F1: the section overview carries the previous period for D-058
async def test_r11_section_overview_has_previous_period(login_wm, provisioned):
    from app.services.overview import _previous

    last = _previous(PERIOD)
    async with provisioned() as s:
        child = await _child(s)
        for sid, spend in (("1", 7), (str(child.id), 5)):
            s.add(SpendSnapshot(scope_kind="group", scope_id=sid, period=last, model=None,
                                spend=spend))  # fmt: skip
        await s.commit()
    body = (await (await login_wm("dana")).get("/budget/overview?scope=group:1")).json()
    assert body["previous_period"]["period"] == last
    assert Decimal(str(body["previous_period"]["used"])) == 12


# review-p5-r11 major B3: an initiative created after the selected period is not in its past
async def test_r12_past_period_ignores_later_initiative(login_wm, provisioned):
    from app.services.overview import _previous

    async with provisioned() as s:
        s.add(Initiative(name="Late", owner_upn=YAEL, group_id=1, department_id=1, budget=100,
                         created_by=YAEL))  # fmt: skip
        await s.commit()
    c = await login_wm("dana")
    past = (await c.get(f"/budget/overview?scope=group:1&period={_previous(PERIOD)}")).json()
    assert not [r for r in past["rows"] if r["kind"] == "initiative"]
    now = (await c.get("/budget/overview?scope=group:1")).json()
    assert [r["total"] for r in now["rows"] if r["kind"] == "initiative"] == [100]
    assert past["kpis"]["allocated"] == now["kpis"]["allocated"] - 100


# review-p5-r11 major B3: a manager's block summary covers failures in descendant teams
async def test_r12_blocks_cover_descendant_team(login_wm, provisioned, fake_app):
    async with provisioned() as s:
        child = await _child(s)
        await s.commit()
    row = dict(team_id=child.litellm_team_id, user_id=YAEL, model="m", status_code=429,
               exception_type="max_parallel_requests", message="limit", retry_after=3,
               at=datetime.now(UTC).isoformat())  # fmt: skip
    transport = httpx.ASGITransport(app=fake_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://fake") as c:
        assert (await c.post("/_test/errors", json=[row])).status_code == 200
    params = {"scope": "group", "id": "1", "from": str(date.today() - timedelta(days=1)),
              "to": str(date.today() + timedelta(days=1))}  # fmt: skip
    r = await (await login_wm("dana")).get("/reports/blocks", params=params)
    assert {x["reason"]: x["count"] for x in r.json()["by_reason"]} == {"RATE_LIMIT": 1}
