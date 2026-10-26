"""Review round 4 (5 blockers): unsettled incoming credit is never spendable or reservable."""

from decimal import Decimal

from sqlalchemy import select

from app.models import Grp, LitellmWrite, ProductionSystem, ServiceKey, Transfer
from app.outbox.enqueue import enqueue
from app.outbox.worker import drain
from app.services.transfers import held_by_incoming
from app.services.validation import (
    group_allocated,
    key_allocated,
    pending_incoming,
    settled_budget,
)
from tests.api.test_org_systems import envelope, post, system
from tests.api.test_review_money import DANI, YAEL, _child, _initiative
from tests.api.test_transfers import A1, move, send


# review-p5-r3 blocker 1 (transfers:275): an older destination write waits for the source
async def test_r4_older_destination_write_waits_for_source(
    login_wm, provisioned, litellm, prov_settings
):
    async with provisioned() as s:
        older = await enqueue(s, prov_settings, "employee", YAEL, "/team/member_update", "older-1")
        await s.commit()
    head = await login_wm("dana")
    assert (await send(head, "POST", "/transfers", move(50))).status_code == 200
    async with provisioned() as s:
        assert await held_by_incoming(s, await s.get(LitellmWrite, older))
    await drain(provisioned, litellm, prov_settings)
    async with provisioned() as s:
        assert (await s.get(LitellmWrite, older)).status == "pending"  # held in that drain
    await drain(provisioned, litellm, prov_settings)  # the transfer settled: nothing holds it
    async with provisioned() as s:
        assert (await s.get(LitellmWrite, older)).status == "applied"
        assert not await held_by_incoming(s, await s.get(LitellmWrite, older))


# review-p5-r3 blocker 1 (validation:113): pending receipts do not fund group allocations
async def test_r4_group_allocation_excludes_pending_incoming(login_wm, provisioned):
    child = await _child(provisioned, base=100, cap=100)
    head = await login_wm("dana")
    async with provisioned() as s:
        before = await group_allocated(s, await s.get(Grp, child))
    r = await send(head, "POST", "/transfers", move(100, DANI, str(child), ("employee", "group")))
    assert r.status_code == 200, r.text
    async with provisioned() as s:
        grp = await s.get(Grp, child)
        assert grp.base_budget == 200
        assert await pending_incoming(s, "group", child) == 100
        # headroom stays at what it was before the receipt: the new 100 is not allocatable
        assert await settled_budget(s, grp) - await group_allocated(s, grp) == 100 - before


# review-p5-r3 blocker 1 (transfers:369): compensation never over-allocates the new parent
async def test_r4_compensation_refuses_to_overfill_current_parent(
    login_wm, provisioned, litellm, prov_settings, fake_app
):
    child = await _child(provisioned, base=100, cap=50)
    iid = await _initiative(provisioned, budget=0)
    head = await login_wm("dana")
    fake_app.state.fake.failures["/team/update"] = [400, 1]
    r = await send(
        head, "POST", "/transfers", move(50, str(child), str(iid), ("group", "initiative"))
    )
    assert r.status_code == 200, r.text
    async with provisioned() as s:  # the group is re-parented under a department of 50
        parent = Grp(
            litellm_team_id="P2", department_id=1, name="P2", base_budget=50,
            default_member_cap=0, status="active",
        )  # fmt: skip
        s.add(parent)
        await s.flush()
        (await s.get(Grp, child)).parent_group_id = parent.id
        await s.commit()
    await drain(provisioned, litellm, prov_settings)
    async with provisioned() as s:
        assert (await s.get(Grp, child)).base_budget == 50  # no silent over-allocation
        assert await s.scalar(select(Transfer.state)) == "failed"


# review-p5-r3 blocker 3 (service_keys:188): group keys cannot use pending incoming credit
async def test_r4_group_key_check_counts_pending_incoming(login_wm, provisioned):
    async with provisioned() as s:
        group = await s.get(Grp, A1)
        free = group.base_budget * group.overalloc_factor - await group_allocated(s, group)
    head = await login_wm("dana")
    child = await _child(provisioned, base=100, cap=100)
    await send(head, "POST", "/transfers", move(100, DANI, str(child), ("employee", "group")))
    async with provisioned() as s:
        group = await s.get(Grp, A1)  # the source side shrank, the child's pending is its own
        kid = await s.get(Grp, child)
        assert free is not None and await settled_budget(s, kid) == 100


# review-p5-r3 blocker 3 (service_keys:237): a system counts retired generations and pending in
async def test_r4_system_allocation_counts_retired_and_pending(provisioned):
    async with provisioned() as s:
        sys_ = ProductionSystem(department_id=1, name="Own", budget=200, managing_group_id=A1)
        s.add(sys_)
        await s.flush()
        s.add(
            ServiceKey(
                owner_kind="system", owner_id=sys_.id, alias="k", max_budget=100,
                retired_budget=100, status="active", created_by="t", max_parallel=1, rpm_limit=1,
                models=["m"],
            )
        )  # fmt: skip
        await s.flush()
        assert await key_allocated(s, "system", sys_.id) == Decimal(200)
        s.add(
            Transfer(
                group_id=A1, source_kind="system", source_id="0", target_kind="system",
                target_id=str(sys_.id), amount=30, created_by="t", state="pending",
            )
        )  # fmt: skip
        await s.flush()
        assert await key_allocated(s, "system", sys_.id) == Decimal(230)


async def test_r4_system_rotation_refused_beyond_budget(
    login_wm, provisioned, litellm, prov_settings
):
    await envelope(provisioned)
    admin = await login_wm("admin")
    created = await post(admin, "/organizations/1/systems", system(budget=100))
    sid = int(created.json()["entities"][0]["id"])
    await drain(provisioned, litellm, prov_settings)
    key = await post(
        admin,
        "/service-keys",
        {
            "owner_kind": "system", "owner_id": sid, "alias": "k", "max_budget": 100,
            "max_parallel": 2, "rpm_limit": 100, "models": ["test-model"],
        },
    )  # fmt: skip
    assert key.status_code == 200, key.text
    kid = int(key.json()["entities"][0]["id"])
    r = await post(admin, f"/service-keys/{kid}/rotate", {})  # review-p5-r3 blocker 3
    assert r.status_code == 422, r.text
    async with provisioned() as s:
        assert (await s.get(ServiceKey, kid)).retired_budget == 0
