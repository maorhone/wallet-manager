"""Phase 5 round-12 re-review (fixed in round 13): regression tests."""

from sqlalchemy import select

from app.models import Grp, ServiceKey
from tests.api.test_review_money import _log
from tests.api.test_review_r10 import _quiet_group

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
