"""Phase 5 round-12 re-review (fixed in round 13): regression tests."""

from sqlalchemy import select

from app.litellm_client import LiteLLMReader
from app.litellm_client.errors import LiteLLMPermanentError
from app.models import Grp, LitellmWrite, ServiceKey, Transfer
from app.outbox.worker import drain
from tests.api.test_review_money import DANI, YAEL, _log
from tests.api.test_review_r10 import _quiet_group
from tests.api.test_transfers import move, send

REP_HEADERS = {"X-Change-Reason": "split"}


async def _split(client, groups):
    body = {"groups": groups, "pmo_pool": 180000, "reserve": 50000}
    headers = {**REP_HEADERS, "X-CSRF-Token": client.cookies["wm_csrf"]}
    return await client.put("/departments/1/split", json=body, headers=headers)


# review-p5-r12 blocker B1b: a split cut cannot release credit a departed member already consumed
async def test_r13_split_cut_keeps_consumed_credit(login_wm, provisioned, fake_engine):
    async with provisioned() as s:
        await _quiet_group(s, budget=100)
        s.add(ServiceKey(owner_kind="group", owner_id=1, max_budget=20, status="active"))
        await s.commit()
    await _log(fake_engine, 1, "A1", "ghost@corp.example", 80)
    rep = await login_wm("rep")
    r = await _split(rep, [{"group_id": 1, "base": 20}])
    assert r.status_code == 422, r.text
    assert r.json()["details"]["reason"] == "below_consumed"
    r = await _split(rep, [{"group_id": 1, "base": 100}])
    assert r.status_code == 200, r.text
    async with provisioned() as s:
        assert (await s.scalar(select(Grp.base_budget).where(Grp.id == 1))) == 100


# review-p5-r12 blocker B1b: with no fresh spend reading a cut is refused, never assumed zero
async def test_r13_split_cut_refused_without_spend_source(login, provisioned):
    async with provisioned() as s:
        await _quiet_group(s, budget=100)
        await s.commit()
    rep = await login("rep", litellm_database_url=None)
    r = await _split(rep, [{"group_id": 1, "base": 20}])
    assert r.status_code == 503, r.text


# review-p5-r12 major B1a1: a failed destination write is settled by the sibling that published it
async def test_r13_failed_destination_write_superseded_by_sibling(
    login_wm, provisioned, litellm, prov_settings, ro_engine
):
    head = await login_wm("dana")
    for amount in (10, 20):
        assert (await send(head, "POST", "/transfers", move(amount, DANI, YAEL))).status_code == 200
    real, calls = litellm.team_member_update, []

    async def flaky(body):
        if body.user_id == YAEL:
            calls.append(body)
            if len(calls) == 1:
                raise LiteLLMPermanentError(400, "injected")
        return await real(body)

    litellm.team_member_update = flaky
    reader = LiteLLMReader(ro_engine)
    await drain(provisioned, litellm, prov_settings, reader)
    litellm.team_member_update = real
    await drain(provisioned, litellm, prov_settings, reader)
    async with provisioned() as s:
        assert set(await s.scalars(select(Transfer.state))) == {"applied"}
        statuses = set(await s.scalars(select(LitellmWrite.status)))
        assert "failed" not in statuses
