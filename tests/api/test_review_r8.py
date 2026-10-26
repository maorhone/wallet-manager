"""Phase 5 round-7 re-review (fixed in round 8): regression tests."""

from sqlalchemy import select

from app.litellm_client import LiteLLMReader
from app.models import Employee, Initiative, Transfer
from app.outbox.worker import drain
from tests.api.test_review_money import DANI, YAEL, _initiative, _log
from tests.api.test_transfers import move, send


# review-p5-r7 blocker B1a1: pending incoming credit never masks consumed funds at the fence
async def test_r8_fence_excludes_unsettled_incoming_credit(
    login_wm, provisioned, fake_engine, litellm, prov_settings, ro_engine
):
    iid = await _initiative(provisioned, budget=0, owner=YAEL)
    head = await login_wm("dana")
    out = move(5000, DANI, str(iid), ("employee", "initiative"))
    assert (await send(head, "POST", "/transfers", out)).status_code == 200
    back = move(5000, YAEL, DANI)  # incoming credit to the source, still unsettled
    assert (await send(head, "POST", "/transfers", back)).status_code == 200
    await _log(fake_engine, 41, "A1", DANI, 12000)  # fits the 15000 local balance, not 10000
    await drain(provisioned, litellm, prov_settings, LiteLLMReader(ro_engine))
    async with provisioned() as s:
        first = await s.scalar(select(Transfer).order_by(Transfer.id))
        assert first.state == "compensated"
        assert (await s.get(Initiative, iid)).budget == 0
        assert (await s.get(Employee, DANI)).base_cap == 20000  # restored 15000 + settled receipt


# review-p5-r7 blocker B1a2: the lookback covers the whole active duration
async def test_r8_window_start_covers_hours_and_multi_month(provisioned):
    from datetime import UTC, datetime

    from app.models import Department
    from app.services.spend import SpendUnavailable, _window_start

    now = datetime(2026, 10, 26, 12, tzinfo=UTC)
    for period, earliest in (("1440h", datetime(2026, 8, 27, 12, tzinfo=UTC)),
                             ("2mo", datetime(2026, 8, 25, 12, tzinfo=UTC))):  # fmt: skip
        async with provisioned() as s:
            (await s.get(Department, 1)).budget_period = period
            await s.commit()
            assert await _window_start(s, now) <= earliest, period
    async with provisioned() as s:
        (await s.get(Department, 1)).budget_period = "soon"
        await s.commit()
        try:
            await _window_start(s, now)
            raise AssertionError("unreadable window must refuse")
        except SpendUnavailable:
            pass
