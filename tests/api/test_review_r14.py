"""Phase 5 round-13 re-review (fixed in round 14): regression tests."""

from sqlalchemy import select

from app.litellm_client import LiteLLMReader, TeamNew
from app.models import Grp, Transfer
from app.outbox.worker import drain
from tests.api.test_review_money import DANI, _child, _log
from tests.api.test_transfers import move, send


# review-p5-r13 blocker B1a1: the late-spend fence counts the group's unspent allocations too
async def test_r14_group_fence_counts_outstanding_allocations(
    login_wm, provisioned, fake_engine, litellm, prov_settings, ro_engine
):
    gid = await _child(provisioned, base=100, cap=50)  # 50 spent-or-unspent funding is committed
    await litellm.team_new(TeamNew(team_id="A1-sub", max_budget=100))
    head = await login_wm("dana")
    out = move(50, str(gid), DANI, ("group", "employee"))
    assert (await send(head, "POST", "/transfers", out)).status_code == 200
    # a departed member's in-flight request lands before settlement: 30 spent + 50 unspent = 80
    await _log(fake_engine, 51, "A1-sub", "ghost@corp.example", 30)
    await drain(provisioned, litellm, prov_settings, LiteLLMReader(ro_engine))
    async with provisioned() as s:
        assert (await s.scalar(select(Transfer.state))) == "compensated"
        assert (await s.get(Grp, gid)).base_budget == 100


# review-p5-r13 major B1a1: a failed, unpublished destination write is cancelled with its source
async def test_r14_failed_destination_cancelled_when_source_overspent(
    login_wm, provisioned, fake_engine, litellm, prov_settings, ro_engine
):
    from app.litellm_client.errors import LiteLLMPermanentError
    from tests.api.test_review_money import YAEL

    gid = await _child(provisioned, base=100, cap=50)
    await litellm.team_new(TeamNew(team_id="A1-sub", max_budget=100))
    head = await login_wm("dana")
    assert (await send(head, "POST", "/transfers", move(5000, DANI, YAEL))).status_code == 200
    out = move(50, str(gid), YAEL, ("group", "employee"))
    assert (await send(head, "POST", "/transfers", out)).status_code == 200
    real, calls = litellm.team_member_update, []

    async def flaky(body):
        if body.user_id == YAEL:
            calls.append(body)
            if len(calls) == 1:  # only the first destination write fails for good
                raise LiteLLMPermanentError(400, "injected")
        return await real(body)

    litellm.team_member_update = flaky
    reader = LiteLLMReader(ro_engine)
    await drain(provisioned, litellm, prov_settings, reader)
    await _log(fake_engine, 52, "A1-sub", "ghost@corp.example", 30)  # 30 + 50 unspent > 50
    litellm.team_member_update = real
    for _ in range(3):
        await drain(provisioned, litellm, prov_settings, reader)
    async with provisioned() as s:
        states = list(await s.scalars(select(Transfer.state).order_by(Transfer.id)))
        assert states == ["applied", "compensated"], states
