"""Review round 3 (12 blockers): one money-movement design, one regression test per finding."""

import asyncio
from decimal import Decimal

from sqlalchemy import select

from app.litellm_client import LiteLLMReader
from app.models import (
    Employee,
    Initiative,
    LitellmWrite,
    Policy,
    ProductionSystem,
    ServiceKey,
    Transfer,
)
from app.outbox.worker import compensate_failed_source, drain
from app.services.production import system_committed
from app.services.spend import Spend
from tests.api.test_review_money import DANI, YAEL, _initiative, _log
from tests.api.test_transfers import A1, move, send


async def _caps(sf):
    async with sf() as s:
        return {
            e.upn: e.base_cap for e in await s.scalars(select(Employee)) if e.upn in (DANI, YAEL)
        }


# review-p5-r2 blocker 1 (org_envelopes:115): a department cannot drop below committed children
async def test_r3_department_reduction_keeps_child_commitments(login_wm, provisioned):
    admin = await login_wm("admin")
    r = await send(admin, "PUT", "/departments/1/budget", {"budget": 1, "budget_period": "1mo"})
    assert r.status_code == 422, r.text


# review-p5-r2 blocker 2 (transfers:121): pending incoming credit is not transferable
async def test_r3_pending_incoming_not_transferable_for_employees(login_wm, provisioned):
    head = await login_wm("dana")
    assert (await send(head, "POST", "/transfers", move(5000))).status_code == 200
    onward = await send(head, "POST", "/transfers", move(15001, YAEL, DANI))
    assert onward.status_code == 422, onward.text
    assert onward.json()["details"]["reason"] == "exceeds_unused"
    ok = await send(head, "POST", "/transfers", move(15000, YAEL, DANI))
    assert ok.status_code == 200, ok.text


# review-p5-r2 blocker 3 (transfers:253): transfers into initiatives are fenced
async def test_r3_initiative_destination_is_fenced(
    login_wm, provisioned, fake_engine, litellm, prov_settings, ro_engine
):
    iid = await _initiative(provisioned, budget=0, owner=YAEL)
    head = await login_wm("dana")
    r = await send(
        head, "POST", "/transfers", move(15000, DANI, str(iid), ("employee", "initiative"))
    )
    assert r.status_code == 200, r.text
    await _log(fake_engine, 31, "A1", DANI, 30)  # the source spends before the reduction applies
    await drain(provisioned, litellm, prov_settings, LiteLLMReader(ro_engine))
    async with provisioned() as s:
        assert (await s.get(Employee, DANI)).base_cap == 15000
        assert (await s.get(Initiative, iid)).budget == 0
        assert (await s.scalar(select(Transfer.state))) == "compensated"


# review-p5-r2 blockers 4 and 9 (transfers:321, worker:161): compensation exactly once
async def test_r3_compensation_is_idempotent_for_initiative(
    login_wm, provisioned, litellm, prov_settings, fake_app
):
    iid = await _initiative(provisioned, budget=0, owner=YAEL)
    head, admin = await login_wm("dana"), await login_wm("admin")
    fake_app.state.fake.failures["/team/member_update"] = [400, 2]
    r = await send(head, "POST", "/transfers", move(50, DANI, str(iid), ("employee", "initiative")))
    assert r.status_code == 200, r.text
    await drain(provisioned, litellm, prov_settings)
    async with provisioned() as s:
        source_write = await s.scalar(
            select(LitellmWrite.id).where(LitellmWrite.status == "failed")
        )
        assert (await s.get(Initiative, iid)).budget == 0
    assert (await send(admin, "POST", f"/outbox/{source_write}/retry")).status_code == 200
    await drain(provisioned, litellm, prov_settings)  # fails again: must not reverse twice
    async with provisioned() as s:
        assert (await s.get(Employee, DANI)).base_cap == 15000
        assert (await s.get(Initiative, iid)).budget == 0


async def test_r3_two_concurrent_compensations_change_balances_once(
    login_wm, provisioned, litellm, prov_settings, fake_app
):
    head = await login_wm("dana")
    fake_app.state.fake.failures["/team/member_update"] = [400, 1]
    assert (await send(head, "POST", "/transfers", move(500))).status_code == 200
    await drain(provisioned, litellm, prov_settings)
    assert await _caps(provisioned) == {DANI: 15000, YAEL: 15000}

    async def again():
        async with provisioned() as s:
            write = await s.scalar(select(LitellmWrite).where(LitellmWrite.status == "failed"))
            await compensate_failed_source(s, write)
            await s.commit()

    await asyncio.gather(again(), again())
    assert await _caps(provisioned) == {DANI: 15000, YAEL: 15000}


# review-p5-r2 blocker 6 (transfers:311): no 500-row window
async def test_r3_old_transfer_still_compensated_after_500_newer(
    login_wm, provisioned, litellm, prov_settings, fake_app
):
    head = await login_wm("dana")
    fake_app.state.fake.failures["/team/member_update"] = [400, 1]
    assert (await send(head, "POST", "/transfers", move(500))).status_code == 200
    async with provisioned() as s:
        s.add_all(
            Transfer(
                group_id=A1, source_kind="employee", source_id="x", target_kind="employee",
                target_id="y", amount=1, created_by="t", state="applied",
            )
            for _ in range(501)
        )  # fmt: skip
        await s.commit()
    await drain(provisioned, litellm, prov_settings)
    assert await _caps(provisioned) == {DANI: 15000, YAEL: 15000}


# review-p5-r2 blocker 7 (bulk:86): consumed credit is not returned to the pool
async def test_r3_bulk_refuses_cap_below_consumed(login_wm, provisioned, fake_engine):
    await _log(fake_engine, 71, "A1", DANI, 80)
    head = await login_wm("dana")
    h = {"X-CSRF-Token": head.cookies["wm_csrf"], "X-Change-Reason": "bulk"}
    body = {"upns": [DANI], "amount": 10}
    r = await head.post(f"/groups/{A1}/bulk-allocation", json=body, headers=h)
    assert r.status_code == 422, r.text
    assert r.json()["details"]["reason"] == "below_consumed"
    ok = await head.post(f"/groups/{A1}/bulk-allocation", json={**body, "amount": 80}, headers=h)
    assert ok.status_code == 200, ok.text


# review-p5-r2 blocker 8 (production:148): historical key generations keep reserving
async def test_r3_rotated_key_reserves_old_generation(provisioned):
    async with provisioned() as s:
        system = ProductionSystem(department_id=1, name="Own", budget=200, managing_group_id=A1)
        s.add(system)
        await s.flush()
        s.add(
            ServiceKey(
                owner_kind="system", owner_id=system.id, alias="k", max_budget=100,
                retired_budget=100, status="active", created_by="t", max_parallel=1, rpm_limit=1,
                models=["m"],
            )
        )  # fmt: skip
        await s.flush()
        assert await system_committed(s, Spend([], "2026-10"), system) == Decimal(200)


# review-p5-r2 blocker 9 (service_keys:196): group keys get the policy floor for non-AI callers
async def test_r3_group_key_limits_are_policy_floor(login_wm, provisioned):
    head = await login_wm("dana")
    body = {
        "owner_kind": "group", "owner_id": A1, "alias": "bot", "max_budget": 10,
        "max_parallel": 9, "rpm_limit": 900, "models": ["test-model"],
    }  # fmt: skip
    r = await send(head, "POST", "/service-keys", body)
    assert r.status_code == 200, r.text
    async with provisioned() as s:
        floor = {k: (await s.get(Policy, k)).value["min"] for k in ("concurrency", "rpm")}
        key = (await s.scalars(select(ServiceKey).where(ServiceKey.owner_kind == "group"))).first()
        assert (key.max_parallel, key.rpm_limit) == (floor["concurrency"], floor["rpm"])
