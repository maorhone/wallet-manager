"""Phase 5 round-10 re-review (fixed in round 11): regression tests."""

from datetime import UTC, datetime
from decimal import Decimal

from app.models import Grp, Initiative, RateCard, ServiceKey, SpendSnapshot
from app.services.spend import Spend
from tests.api.test_review_r8 import _audit, _tokens
from tests.api.test_review_r10 import PERIOD, YAEL, _quiet_group

TZ = "Asia/Jerusalem"


# review-p5-r10 major B1a1: a key moved to a system keeps counting as the initiative's spend
async def test_r11_group_committed_counts_moved_key_spend(provisioned):
    from app.services.transfers import group_committed

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
