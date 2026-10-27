"""Review round 6 (round-5 re-review): blockers and the backend money majors."""

import asyncio
from decimal import Decimal

from sqlalchemy import func, select

from app.db import LockKind, lock_parent
from app.litellm_client import LiteLLMReader
from app.models import AuditLog, Employee, Grp, LitellmWrite, ProductionSystem, ServiceKey, Transfer
from app.outbox.enqueue import enqueue
from app.outbox.worker import drain
from app.services.production import system_committed
from app.services.spend import Spend
from app.services.transfers import lock_scope, transfer_status
from app.services.validation import group_allocated
from tests.api.test_review_money import DANI, YAEL, _child, _initiative
from tests.api.test_transfers import A1, move, send


def _pending(kind, ident, amount=100, state="pending"):
    return Transfer(
        group_id=A1, source_kind="employee", source_id="x", target_kind=kind,
        target_id=str(ident), amount=amount, created_by="t", state=state,
    )  # fmt: skip


# review-p5-r5 blocker 1b/1c (org_envelopes:148, production:152): revoked/failed keys stay committed
async def test_r6_system_committed_keeps_revoked_and_failed_keys(provisioned):
    async with provisioned() as s:
        sys_ = ProductionSystem(department_id=1, name="Own", budget=200, managing_group_id=A1)
        s.add(sys_)
        await s.flush()
        for status, retired in (("revoked", 100), ("failed", 0)):
            s.add(
                ServiceKey(
                    owner_kind="system",
                    owner_id=sys_.id,
                    alias=status,
                    max_budget=50,
                    retired_budget=retired,
                    status=status,
                    created_by="t",
                    max_parallel=1,
                    rpm_limit=1,
                    models=["m"],
                )  # fmt: skip
            )
        await s.flush()
        assert await system_committed(s, Spend([], "2026-10"), sys_) == Decimal(100 + 50 + 50)


# review-p5-r5 blocker 1b/3 (validation:171, departments:96): a split cannot release pending credit
async def test_r6_split_refuses_leaf_below_pending_incoming(login_wm, provisioned):
    child = await _child(provisioned, base=100, cap=0)
    head = await login_wm("dana")
    r = await send(head, "POST", "/transfers", move(100, DANI, str(child), ("employee", "group")))
    assert r.status_code == 200, r.text
    rep = await login_wm("rep")
    body = {"groups": [{"group_id": child, "base": 0}], "pmo_pool": 180000, "reserve": 50000}
    headers = {"X-CSRF-Token": rep.cookies["wm_csrf"], "X-Change-Reason": "split"}
    r = await rep.put("/departments/1/split", json=body, headers=headers)
    assert r.status_code == 422, r.text
    async with provisioned() as s:
        assert (await s.get(Grp, child)).base_budget == 200


# review-p5-r5 blocker 3 (members:75): direct member-cap writes keep the pending-receipt floor
async def test_r6_direct_member_caps_floor_pending_receipt(login_wm, provisioned):
    head = await login_wm("dana")
    assert (await send(head, "POST", "/transfers", move(100))).status_code == 200
    r = await send(head, "PUT", f"/groups/{A1}/members/caps", [{"upn": YAEL, "base_cap": 0}])
    assert r.status_code == 422, r.text
    assert r.json()["details"]["reason"] == "below_consumed"
    async with provisioned() as s:
        assert (await s.get(Employee, YAEL)).base_cap == 15100


# review-p5-r5 blocker 3 (members:75): the group is re-read after its lock was acquired
async def test_r6_set_caps_sees_budget_changed_while_waiting(login_wm, provisioned):
    head = await login_wm("dana")
    async with provisioned() as s:
        group = await s.get(Grp, A1)
        total = await group_allocated(s, group)
        group.base_budget = total  # fully allocated
        await s.commit()
    blocker = provisioned()
    async with blocker as s:
        await lock_parent(s, LockKind.GROUP, A1)  # a transfer-like writer holds the group
        task = asyncio.create_task(
            send(head, "PUT", f"/groups/{A1}/members/caps", [{"upn": YAEL, "base_cap": 15100}])
        )
        await asyncio.sleep(0.3)
        assert not task.done()
        (await s.get(Grp, A1)).base_budget = total - 50  # budget reduced while the writer waits
        await s.commit()
    r = await task
    assert r.status_code == 422, r.text  # +100 against a budget that is now below the allocation


# review-p5-r5 blocker 1a1/3 (transfers:449, worker:136): worker waits for transfer's money locks
async def test_r6_worker_serializes_with_transfer_creation(
    login_wm, provisioned, litellm, prov_settings
):
    async with provisioned() as s:
        older = await enqueue(s, prov_settings, "employee", YAEL, "/team/member_update", "o-1")
        await s.commit()
    async with provisioned() as holder:
        group = await holder.get(Grp, A1)
        await lock_scope(holder, group, [("employee", DANI), ("employee", YAEL)])
        task = asyncio.create_task(drain(provisioned, litellm, prov_settings))
        await asyncio.sleep(0.5)
        assert not task.done()  # the older destination write waits behind the transfer's locks
        await holder.rollback()
    await asyncio.wait_for(task, 30)
    async with provisioned() as s:
        assert (await s.get(LitellmWrite, older)).status == "applied"


# review-p5-r5 majors (transfers:454, worker:136): receipts to one target never block each other
async def test_r6_two_transfers_to_one_target_settle(
    login_wm, provisioned, litellm, prov_settings, ro_engine
):
    head = await login_wm("dana")
    assert (await send(head, "POST", "/transfers", move(50))).status_code == 200
    assert (await send(head, "POST", "/transfers", move(50))).status_code == 200
    for _ in range(4):
        await drain(provisioned, litellm, prov_settings, LiteLLMReader(ro_engine))
    async with provisioned() as s:
        states = list(await s.scalars(select(Transfer.state).order_by(Transfer.id)))
    assert states == ["applied", "applied"]


# review-p5-r5 major (transfers:392): compensation counts employee/initiative decreases
async def test_r6_compensation_nets_employee_change(
    login_wm, provisioned, litellm, prov_settings, fake_app
):
    child = await _child(provisioned, base=100, cap=50)
    async with provisioned() as s:
        group = await s.get(Grp, A1)
        group.base_budget = await group_allocated(s, group)  # fully allocated parent
        await s.commit()
    head = await login_wm("dana")
    fake_app.state.fake.failures["/team/update"] = [400, 1]
    r = await send(head, "POST", "/transfers", move(20, str(child), DANI, ("group", "employee")))
    assert r.status_code == 200, r.text
    tid = int(r.json()["entities"][0]["id"])
    await drain(provisioned, litellm, prov_settings)
    async with provisioned() as s:
        assert (await s.get(Transfer, tid)).state == "compensated"
        assert (await s.get(Grp, child)).base_budget == 100
        assert (await s.get(Employee, DANI)).base_cap == 15000
        # review-p5-r5 major (transfers:381): exactly one reversal audit row
        audits = await s.scalar(
            select(func.count()).where(
                AuditLog.entity == "transfer_compensation", AuditLog.entity_id == str(tid)
            )
        )
        assert audits == 1
    assert (await head.get(f"/transfers/{tid}")).json()["status"] == "failed"


# review-p5-r5 major (transfers:311): lifecycle state decides the reported status
async def test_r6_status_follows_transfer_state(provisioned):
    iid = await _initiative(provisioned)
    async with provisioned() as s:
        w = LitellmWrite(
            entity="employee",
            entity_id=DANI,
            endpoint="/team/member_update",
            status="applied",
            idempotency_key="st-1",
        )
        s.add(w)
        await s.flush()
        for state, expected in (
            ("pending", "pending_apply"),
            ("compensated", "failed"),
            ("applied", "done"),
        ):
            t = Transfer(
                group_id=A1, source_kind="employee", source_id=DANI, target_kind="initiative",
                target_id=str(iid), amount=1, created_by="t", state=state,
                write_ids=[w.id], first_write_id=w.id,
            )  # fmt: skip
            assert await transfer_status(s, t) == expected
