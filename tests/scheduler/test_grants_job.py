import asyncio
from datetime import timedelta

from helpers.grants import A1, DANI, YAEL, last_id, membership, new_grant, new_share, outbox
from sqlalchemy import func, select

from app.models import AuditLog, Grant, GrantShare
from app.outbox.worker import drain


async def team_budget(litellm) -> float:
    return (await litellm.team_info("A1")).team_info.max_budget


async def test_pending_grant_activates_at_start_time(
    login_wm, provisioned, litellm, prov_settings, run_job, fake_clock
):
    gid = await new_grant(login_wm, starts=2)
    await drain(provisioned, litellm, prov_settings)
    base = await team_budget(litellm)
    assert (await run_job()).activated == 0
    fake_clock.advance(days=3)
    start = await last_id(provisioned)
    assert (await run_job()).activated == 1
    async with provisioned() as s:
        assert (await s.get(Grant, gid)).status == "active"
        actor = await s.scalar(select(AuditLog.actor_upn).where(AuditLog.entity == "grant"))
    assert actor in ("system:scheduler", "pmo@corp.example")
    assert len(await outbox(provisioned, "/team/update", A1, start)) == 1
    await drain(provisioned, litellm, prov_settings)
    assert await team_budget(litellm) == base + 50000


async def _two_shares(login_wm):
    g1 = await new_grant(login_wm)
    g2 = await new_grant(login_wm)
    s1 = await new_share(login_wm, g1, amount=3000, days=10)
    s2 = await new_share(login_wm, g2, amount=2000, days=20)
    return g1, g2, s1, s2


async def test_share_expiry_closes_returns_and_recomputes_next_share(
    login_wm, provisioned, litellm, prov_settings, run_job, fake_clock
):
    g1, g2, s1, s2 = await _two_shares(login_wm)
    await drain(provisioned, litellm, prov_settings)
    assert (await membership(litellm)).temp_budget_increase == 5000
    before = await team_budget(litellm)
    fake_clock.advance(days=11)
    start = await last_id(provisioned)
    res = await run_job()
    assert (res.shares_closed, res.grants_closed, res.members_recomputed) == (1, 0, 1)
    async with provisioned() as s:
        sh, g = await s.get(GrantShare, s1), await s.get(Grant, g1)
        assert (sh.status, sh.closed_reason, sh.used, sh.returned) == ("closed", "expiry", 0, 3000)
        assert g.returned == 3000
        assert (await s.get(GrantShare, s2)).status == "active"
    assert len(await outbox(provisioned, "/team/member_update", DANI, start)) == 1
    await drain(provisioned, litellm, prov_settings)
    budget = await membership(litellm)
    assert budget.temp_budget_increase == 2000
    assert abs(budget.temp_budget_expiry - (fake_clock.now() + timedelta(days=9))) < timedelta(
        minutes=5
    )
    assert await team_budget(litellm) == before - 3000


async def test_grant_expiry_closes_all_shares_and_unshared_remainder(
    login_wm, provisioned, litellm, prov_settings, run_job, fake_clock
):
    gid = await new_grant(login_wm, expires=30)
    s1 = await new_share(login_wm, gid, DANI, 3000, days=25)
    s2 = await new_share(login_wm, gid, YAEL, 2000, days=25)
    async with provisioned() as s:  # shares outlive the grant, as after a shortened expiry
        (await s.get(Grant, gid)).expires_at = fake_clock.now() + timedelta(days=20)
        await s.commit()
    await drain(provisioned, litellm, prov_settings)
    fake_clock.advance(days=21)
    res = await run_job()
    assert (res.shares_closed, res.grants_closed, res.members_recomputed) == (0, 1, 2)
    async with provisioned() as s:
        g = await s.get(Grant, gid)
        shares = [await s.get(GrantShare, i) for i in (s1, s2)]
    assert [(x.status, x.closed_reason) for x in shares] == [("closed", "expiry")] * 2
    assert (g.status, g.used, g.returned) == ("closed", 0, 50000)
    await drain(provisioned, litellm, prov_settings)
    for upn in (DANI, YAEL):
        assert not (await membership(litellm, upn)).temp_budget_increase


async def test_run_twice_is_noop(login_wm, provisioned, litellm, run_job, fake_clock):
    await _two_shares(login_wm)
    fake_clock.advance(days=11)
    first = await run_job()
    assert first.shares_closed == 1
    mark = await last_id(provisioned)
    async with provisioned() as s:
        audits = await s.scalar(select(func.count()).select_from(AuditLog))
    second = await run_job()
    assert (second.activated, second.shares_closed, second.grants_closed) == (0, 0, 0)
    assert second.members_recomputed == 0 and await last_id(provisioned) == mark
    async with provisioned() as s:
        assert await s.scalar(select(func.count()).select_from(AuditLog)) == audits


async def test_two_concurrent_runs_never_double_close(
    login_wm, provisioned, litellm, run_job, fake_clock
):
    g1, _, s1, _ = await _two_shares(login_wm)
    fake_clock.advance(days=11)
    a, b = await asyncio.gather(run_job(), run_job())
    assert a.shares_closed + b.shares_closed == 1
    async with provisioned() as s:
        assert (await s.get(Grant, g1)).returned == 3000
        closes = await s.scalar(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.entity == "grant_share", AuditLog.entity_id == str(s1),
                AuditLog.actor_upn == "system:scheduler",
            )
        )  # fmt: skip
        assert closes == 1


async def test_restart_catches_up_on_overdue(
    login_wm, provisioned, litellm, prov_settings, run_job, fake_clock
):
    pending = await new_grant(login_wm, starts=2, expires=5)
    g1, g2, s1, s2 = await _two_shares(login_wm)
    await drain(provisioned, litellm, prov_settings)
    fake_clock.advance(days=40)  # the scheduler was down the whole time
    res = await run_job()
    assert (res.activated, res.shares_closed, res.grants_closed) == (1, 2, 3)
    async with provisioned() as s:
        grants = [await s.get(Grant, i) for i in (pending, g1, g2)]
        assert [g.status for g in grants] == ["closed"] * 3
        assert [(await s.get(GrantShare, i)).status for i in (s1, s2)] == ["closed"] * 2
    await drain(provisioned, litellm, prov_settings)
    assert not (await membership(litellm)).temp_budget_increase
