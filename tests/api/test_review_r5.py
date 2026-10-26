"""Review round 5 (10 blockers). ONE rule: unsettled incoming credit is excluded from the base
before the over-allocation factor applies; system commitments are complete (never shrink)."""

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.litellm_client.readonly import SpendRow
from app.models import Grp, LitellmWrite, ProductionSystem, ServiceKey, Transfer
from app.outbox.enqueue import enqueue
from app.services.production import system_committed
from app.services.spend import Spend, _aggregate
from app.services.transfers import held_by_incoming
from app.services.validation import (
    check_sum,
    group_allocated,
    key_allocated,
    pending_incoming,
    settled_budget,
)
from tests.api.test_review_money import DANI, YAEL
from tests.api.test_transfers import move, send

A1 = 1


def _key(owner_id, **kw):
    return ServiceKey(
        owner_kind="system", owner_id=owner_id, alias="k", max_budget=100, retired_budget=0,
        status="active", created_by="t", max_parallel=1, rpm_limit=1, models=["m"], **kw,
    )  # fmt: skip


def _pending(target_kind, target_id, amount=100, state="pending"):
    return Transfer(
        group_id=A1, source_kind="employee", source_id="x", target_kind=target_kind,
        target_id=str(target_id), amount=amount, created_by="t", state=state,
    )  # fmt: skip


# review-p5-r4 blocker 2 (spend:71): a null model_group is counted once
def test_r5_null_model_group_counted_once():
    rows = [SpendRow("T", "u@corp.example", None, None, date(2026, 10, 1), Decimal(30))]
    maps = ({}, {}, {}, {})
    groups = type("G", (), {"id": 1, "department_id": 1})()
    totals, _ = _aggregate(rows, "2026-10", ({"T": groups}, *maps[1:]))
    assert totals[("group", "1", "2026-10", None)] == Decimal(30)


# review-p5-r4 blocker 1a1 (transfers:454): unrelated destination writes wait for the fence
async def test_r5_destination_write_held_until_transfer_applied(
    login_wm, provisioned, prov_settings
):
    async with provisioned() as s:
        older = await enqueue(s, prov_settings, "employee", YAEL, "/team/member_update", "o-1")
        await s.commit()
    head = await login_wm("dana")
    assert (await send(head, "POST", "/transfers", move(50))).status_code == 200
    async with provisioned() as s:
        transfer = await s.get(Transfer, 1)
        source = await s.get(LitellmWrite, transfer.first_write_id)
        source.status = "applied"  # source applied, fence not passed: transfer still pending
        await s.commit()
        assert transfer.state == "pending"
        assert await held_by_incoming(s, await s.get(LitellmWrite, older))


# review-p5-r4 blockers 1b (validation:121) and 3 (service_keys:178): factor applies to settled
async def test_r5_pending_not_multiplied_by_factor(provisioned):
    async with provisioned() as s:
        group = await s.get(Grp, A1)
        group.overalloc_factor = Decimal(2)
        group.base_budget = Decimal(200)  # 100 settled + 100 pending incoming
        s.add(_pending("group", A1, 100))
        await s.flush()
        assert await settled_budget(s, group) == Decimal(100)
        allocated = await group_allocated(s, group)
        room = await settled_budget(s, group) * 2 - allocated
        assert check_sum(allocated + room + 1, await settled_budget(s, group), 2) is not None
        assert check_sum(allocated + room, await settled_budget(s, group), 2) is None


# review-p5-r4 blockers 1b (validation:98) and 3 (service_keys:181): revoked keys stay reserved
async def test_r5_system_commitment_survives_revocation(provisioned):
    async with provisioned() as s:
        sys_ = ProductionSystem(department_id=1, name="Own", budget=200, managing_group_id=A1)
        s.add(sys_)
        await s.flush()
        s.add(
            _key(
                sys_.id,
            )
            if False
            else _key(sys_.id)
        )
        await s.flush()
        live = await key_allocated(s, "system", sys_.id)
        k = await s.scalar(select(ServiceKey))
        k.retired_budget, k.status = Decimal(100), "revoked"
        await s.flush()
        assert await key_allocated(s, "system", sys_.id) == live + 100  # nothing disappears


# review-p5-r4 blocker 1c (production:195): reductions keep unsettled incoming credit
async def test_r5_system_committed_includes_pending_incoming(provisioned):
    async with provisioned() as s:
        sys_ = ProductionSystem(department_id=1, name="Own", budget=150, managing_group_id=A1)
        s.add(sys_)
        await s.flush()
        s.add(_pending("system", sys_.id, 50))
        await s.flush()
        assert await system_committed(s, Spend([], "2026-10"), sys_) == Decimal(50)


# review-p5-r4 blocker 1b (bulk:108): the floor helper sees a recipient's unsettled credit
async def test_r5_employee_pending_incoming_visible(provisioned):
    async with provisioned() as s:
        s.add(_pending("employee", DANI, 10))
        await s.flush()
        assert await pending_incoming(s, "employee", DANI) == Decimal(10)
