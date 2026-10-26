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


# review-p5-r7 major B2: an owner of a department-funded internal system finds it
async def test_r8_my_systems_include_department_funded(login_wm, provisioned):
    from tests.api.test_review_r7 import _owned

    sid, _ = await _owned(provisioned)
    mine = (await (await login_wm("yael")).get("/systems/mine")).json()["items"]
    assert [(i["id"], i["funding"]) for i in mine] == [(sid, "department")]
    assert (await (await login_wm("dani")).get("/systems/mine")).json()["items"] == []


# review-p5-r7 major B3: key moves lock organization -> owner (like envelope/split writes)
async def test_r8_move_key_locks_organization_before_owner(
    login_wm, provisioned, litellm, prov_settings, monkeypatch
):
    from app.writes import WriteContext
    from tests.api.test_org_systems import envelope, post, system

    await envelope(provisioned)
    admin = await login_wm("admin")
    created = await post(admin, "/organizations/1/systems", system(budget=40000))
    target = int(created.json()["entities"][0]["id"])
    key = await post(
        admin, "/service-keys",
        {"owner_kind": "group", "owner_id": 1, "alias": "bot", "max_budget": 10,
         "max_parallel": 2, "rpm_limit": 100, "models": ["test-model"]},
    )  # fmt: skip
    key_id = int(key.json()["entities"][0]["id"])
    await drain(provisioned, litellm, prov_settings)
    order = []
    real = WriteContext.lock

    async def lock(self, kind, id):
        order.append(kind.name)
        await real(self, kind, id)

    monkeypatch.setattr(WriteContext, "lock", lock)
    r = await post(admin, f"/service-keys/{key_id}/move", {"target_system_id": target})
    assert r.status_code == 200, r.text
    assert order.index("PRODUCTION") < order.index("GROUP"), order


# review-p5-r7 major B1b: a live parent budget below its live child budgets is a finding
async def test_r8_live_parent_below_live_children(login_wm, provisioned, litellm):
    from app.litellm_client import TeamUpdate
    from app.models import Grp
    from tests.api.test_overallocation import _report

    async with provisioned() as s:
        kid = await s.scalar(select(Grp).where(Grp.litellm_team_id == "A2"))
        kid.parent_group_id = 1
        await s.commit()
    await litellm.team_update(TeamUpdate(team_id="A2", max_budget=5000000))
    found = [f for f in await _report(await login_wm("admin")) if f["level"] == "group"]
    assert [(f["source"], f["entity_id"]) for f in found] == [("litellm", 1)]
