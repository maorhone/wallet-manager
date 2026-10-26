"""Phase 5 round-8 re-review (fixed in round 9): regression tests."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.litellm_client import LiteLLMReader
from app.litellm_client.errors import LiteLLMPermanentError
from app.models import Department, Employee, LitellmWrite, Transfer
from app.outbox.worker import drain
from tests.api.test_review_money import DANI, YAEL, _initiative
from tests.api.test_transfers import move, send


class _Unreadable:
    async def spend_rows(self, *args, **kwargs):
        raise RuntimeError("spend database unreachable")


# review-p5-r8 blocker 1: an initiative-origin receipt never bypasses a sibling's unreadable fence
async def test_r9_initiative_origin_receipt_waits_for_sibling_fence(
    login_wm, provisioned, litellm, prov_settings
):
    iid = await _initiative(provisioned, budget=5000, owner=YAEL)
    head = await login_wm("dana")
    assert (await send(head, "POST", "/transfers", move(10, DANI, YAEL))).status_code == 200
    r = await send(head, "POST", "/transfers", move(10, str(iid), YAEL, ("initiative", "employee")))
    assert r.status_code == 200, r.text
    await drain(provisioned, litellm, prov_settings, _Unreadable())
    async with provisioned() as s:
        ids = sorted(w for t in await s.scalars(select(Transfer)) for w in t.write_ids)
        statuses = list(
            await s.scalars(
                select(LitellmWrite.status)
                .where(LitellmWrite.id.in_(ids))
                .order_by(LitellmWrite.id)
            )
        )
        assert statuses == ["applied", "pending", "pending"]  # source only; both receipts held
        assert set(await s.scalars(select(Transfer.state))) == {"pending"}


# review-p5-r8 blocker 2: a failed second source write restores the upstream budget too
async def test_r9_failed_second_source_write_restores_upstream_budget(
    login_wm, provisioned, litellm, prov_settings, ro_engine
):
    head = await login_wm("dana")
    for amount in (10, 20):
        assert (await send(head, "POST", "/transfers", move(amount, DANI, YAEL))).status_code == 200
    published, real = [], litellm.team_member_update

    async def flaky(body):
        published.append((body.user_id, body.max_budget_in_team))
        if sum(1 for u, _ in published if u == DANI) == 2:
            raise LiteLLMPermanentError(400, "injected")
        return await real(body)

    litellm.team_member_update = flaky
    reader = LiteLLMReader(ro_engine)
    await drain(provisioned, litellm, prov_settings, reader)
    litellm.team_member_update = real
    await drain(provisioned, litellm, prov_settings, reader)
    async with provisioned() as s:
        dani = (await s.get(Employee, DANI)).base_cap
        assert dani == 14990  # 15000 - the 10 that stayed (the first write published 14970)
        last = await s.scalar(
            select(LitellmWrite).where(
                LitellmWrite.entity_id == DANI, LitellmWrite.status == "applied"
            ).order_by(LitellmWrite.id.desc())
        )  # fmt: skip
        assert last.payload["max_budget_in_team"] == float(dani)


# review-p5-r8 blocker 3: the lookback subtracts elapsed time across a DST change
async def test_r9_window_start_subtracts_in_utc(provisioned):
    from app.services.spend import _window_start

    now = datetime(2026, 4, 5, 12, tzinfo=ZoneInfo("Asia/Jerusalem"))
    async with provisioned() as s:
        (await s.get(Department, 1)).budget_period = "1440h"
        await s.commit()
        assert await _window_start(s, now) == datetime(2026, 2, 4, 9, tzinfo=UTC)


# review-p5-r8 major B3 (overview:129): the move day is split by the spend log's team
def test_r9_key_spend_splits_by_team_on_the_move_day():
    from datetime import date
    from decimal import Decimal
    from types import SimpleNamespace

    from app.services.spend import _aggregate

    def row(team, spend):
        return SimpleNamespace(
            team_id=team, user=None, api_key="k1", day=date(2026, 10, 10), spend=Decimal(spend),
            model_group=None, prompt_tokens=0, completion_tokens=0,
        )  # fmt: skip

    group = SimpleNamespace(id=1, department_id=1)
    maps = ({"A1": group}, {"S1": SimpleNamespace(id=7)}, {"k1": 5}, {})
    totals, _ = _aggregate([row("A1", 10), row("S1", 30)], "2026-10", maps)
    assert totals[("service_key", "5", "2026-10", None)] == 40
    assert totals[("service_key_group", "5", "2026-10", None)] == 10  # only the group-team part
    assert totals[("system", "7", "2026-10", None)] == 30


# review-p5-r8 major B3 (overview:92): an unspent grant share is allocated, not available
async def test_r9_overview_counts_grant_shares_as_allocated(login_wm, provisioned):
    from app.models import Grant, GrantShare
    from tests.api.test_transfers import A1

    async with provisioned() as s:
        for e in await s.scalars(select(Employee).where(Employee.group_id == A1)):
            e.base_cap = 0
        grant = Grant(group_id=A1, department_id=1, amount=100, returned=0, status="active", model=None)
        s.add(grant)
        await s.flush()
        s.add(GrantShare(grant_id=grant.id, upn=DANI, amount=100, status="active"))
        await s.commit()
    bar = (await (await login_wm("dana")).get("/budget/overview?scope=group:1")).json()["bar"]
    assert bar["allocated_unused"] >= 100


# review-p5-r8 major B3 (rate_card:105): rate intervals follow the consumption timezone
async def test_r9_rates_use_local_days_and_refuse_same_day_changes(provisioned):
    from decimal import Decimal

    from app.models import RateCard
    from app.services.rate_card import metered_value
    from tests.api.test_review_r8 import _audit, _tokens

    old, new = {"input_per_1m": 10, "output_per_1m": 0}, {"input_per_1m": 20, "output_per_1m": 0}
    async with provisioned() as s:
        s.add(RateCard(model="gpt-4", input_per_1m=20, output_per_1m=0))
        await _audit(s, "rate_card", "gpt-4", None, old, datetime(2026, 5, 1, tzinfo=UTC))
        # 00:30 on June 16 in Jerusalem: June 15 is wholly before it, June 16 straddles it
        await _audit(s, "rate_card", "gpt-4", old, new, datetime(2026, 6, 15, 21, 30, tzinfo=UTC))
        await _tokens(s, "2026-06-15", "gpt-4", 1_000_000)
        await s.commit()
        assert await metered_value(s, "group", 1, "2026-06", "Asia/Jerusalem") == Decimal(10)
        await _tokens(s, "2026-06-16", "gpt-4", 1_000_000)
        await s.commit()
        assert await metered_value(s, "group", 1, "2026-06", "Asia/Jerusalem") is None
