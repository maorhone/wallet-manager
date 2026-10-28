"""redesign U1-fix1: regressions from the focused review of U1."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.litellm_client import LiteLLMReader
from app.models import Employee, Grant, GrantShare, Grp, Initiative, LitellmWrite, Transfer
from app.outbox.worker import drain
from app.services import commitments
from app.services.attribution import ShareSpan
from app.services.spend import fresh_spend
from app.services.transfers import _overallocates_parent
from tests.api.test_nested_group_budget import child_of
from tests.api.test_review_money import DANI, YAEL, _initiative
from tests.api.test_transfers import A1, move, send
from tests.helpers.commit import consume

D = Decimal


def _day(n):
    return datetime(2026, 1, n, tzinfo=UTC)


# redesign U1-fix1 closed-window: a closed share's CUMULATIVE usage is bounded by this window
def test_u1fix1_closed_share_usage_is_bounded_by_this_windows_overage():
    share = ShareSpan(1, D(500), _day(20), used=D(100))  # closed in an earlier month
    member = commitments.MemberRow("m", D(100), D(100), shares=(share,))
    snap = commitments.compute(commitments.Rows(D(100), members=(member,)))
    assert (snap.consumption_pmo, snap.consumption_base) == (0, 100)
    # the same share DOES fund overage that exists in this window, but only that much
    member = commitments.MemberRow("m", D(100), D(130), shares=(share,))
    snap = commitments.compute(commitments.Rows(D(130), members=(member,)))
    assert (snap.consumption_pmo, snap.consumption_base) == (30, 100)


# redesign U1-fix1 moved-out: a member who moved on keeps the PMO split in the old group
async def test_u1fix1_moved_out_member_keeps_pmo_attribution(
    login_wm, provisioned, fake_engine, ro_engine
):
    now = datetime.now(UTC)
    async with provisioned() as s:
        grant = Grant(
            department_id=1, group_id=A1, amount=90000, status="active",
            starts_at=now - timedelta(days=2), expires_at=now + timedelta(days=20),
        )  # fmt: skip
        s.add(grant)
        await s.flush()
        s.add(
            GrantShare(
                grant_id=grant.id,
                upn=DANI,
                amount=90000,
                status="closed",
                used=D(90000),
                closed_reason="move",
                expires_at=now + timedelta(days=20),
            )  # fmt: skip
        )
        (await s.get(Employee, DANI)).group_id = 2  # moved to A2 after the PMO-funded spend
        await s.commit()
    await consume(fake_engine, 100000, user=DANI)  # 15000 base + 85000 PMO (bounded by overage)
    async with provisioned() as s:
        spend = await fresh_spend(s, LiteLLMReader(ro_engine), now.astimezone())
        snap = await commitments.snapshot(s, await s.get(Grp, A1), spend, tz="UTC")
    assert snap.consumption_pmo == 85000 and snap.consumption_base == 15000


# redesign U1-fix1 restore-recompute: nonlinear commitments, a moved child with late spend
async def test_u1fix1_restore_is_recomputed_not_added(
    login_wm, provisioned, seeded, fake_engine, ro_engine
):
    child = await child_of(seeded, A1, 60)
    await consume(fake_engine, 80, user="ghost@corp.example", team="A1-sub")
    async with provisioned() as s:
        parent = await s.get(Grp, A1)
        parent.base_budget = D(100)
        for e in await s.scalars(select(Employee).where(Employee.group_id == A1)):
            e.base_cap = 0
        kid = await s.get(Grp, child)
        # restoring 40 makes base 100 = the 80 of spend + 20 reserved: the parent holds exactly 100
        rows = [(kid, "base_budget", D(100))]
        reader = LiteLLMReader(ro_engine)
        assert not await _overallocates_parent(s, None, rows, {str(child): A1}, reader, "UTC")
        rows = [(kid, "base_budget", D(120))]
        assert await _overallocates_parent(s, None, rows, {str(child): A1}, reader, "UTC")


# redesign U1-fix1 retryable: an unreadable spend never strands a transfer as "failed"
async def test_u1fix1_unreadable_spend_keeps_compensation_retryable(
    login_wm, provisioned, litellm, prov_settings, fake_app, ro_engine
):
    iid = await _initiative(provisioned, budget=0, owner=YAEL)
    head = await login_wm("dana")
    fake_app.state.fake.failures["/team/member_update"] = [400, 2]
    r = await send(head, "POST", "/transfers", move(50, DANI, str(iid), ("employee", "initiative")))
    assert r.status_code == 200, r.text
    await drain(provisioned, litellm, prov_settings)  # no reader: compensation must wait
    async with provisioned() as s:
        assert await s.scalar(select(Transfer.state)) == "pending"
        assert (
            await s.scalar(select(LitellmWrite.status).where(LitellmWrite.status == "failed"))
            is None
        )
        assert (await s.get(Initiative, iid)).budget == 50
    await drain(provisioned, litellm, prov_settings, LiteLLMReader(ro_engine))
    async with provisioned() as s:
        assert await s.scalar(select(Transfer.state)) == "compensated"
        assert (await s.get(Initiative, iid)).budget == 0
        assert (await s.get(Employee, DANI)).base_cap == 15000
