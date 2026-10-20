import asyncio

from lifecycle import act
from sqlalchemy import select, text
from test_shares import DANI, body, new_grant, share

from app.db import LockKind
from app.models import Grant, GrantShare, LitellmWrite
from app.outbox.worker import drain
from app.scheduler.jobs import _expire_share
from app.services import lifecycle, shares
from app.writes import WriteContext


async def test_review_expiry_commit_contains_recompute(
    login_wm, provisioned, litellm, prov_settings, fake_clock, ro_engine
):
    from app.litellm_client import LiteLLMReader

    gid = await new_grant(login_wm)
    r = await share(await login_wm("dana"), gid, body(days=1))
    sid = int(r.json()["entities"][0]["id"])
    await drain(provisioned, litellm, prov_settings)
    fake_clock.advance(days=2)
    # Simulate process termination after the closing transaction, before any later phase.
    assert (
        await _expire_share(
            provisioned, prov_settings, sid, 1, fake_clock.now(), litellm, LiteLLMReader(ro_engine)
        )
        == DANI
    )
    async with provisioned() as db:
        assert await db.scalar(
            select(LitellmWrite.id).where(
                LitellmWrite.entity_id == DANI,
                LitellmWrite.endpoint == "/team/member_update",
                LitellmWrite.status == "pending",
            )
        )


async def test_review_stale_closure_never_returns_twice(
    login_wm, provisioned, litellm, prov_settings, fake_clock, ro_engine
):
    from app.litellm_client import LiteLLMReader

    gid = await new_grant(login_wm)
    r = await share(await login_wm("dana"), gid, body(days=1))
    sid = int(r.json()["entities"][0]["id"])
    await drain(provisioned, litellm, prov_settings)
    reader = LiteLLMReader(ro_engine)
    async with provisioned() as stale:
        cached = await stale.get(GrantShare, sid)
        fake_clock.advance(days=2)
        await _expire_share(provisioned, prov_settings, sid, 1, fake_clock.now(), litellm, reader)
        ctx = WriteContext(stale, prov_settings, "system:test", "stale-close", "test")
        await ctx.lock(LockKind.GROUP, 1)
        await shares.close_shares(ctx, [cached], fake_clock.now(), "move", litellm, reader)
        await stale.commit()
    async with provisioned() as db:
        g, s = await db.get(Grant, gid), await db.get(GrantShare, sid)
        assert g.returned == s.returned == 3000
        assert s.used + s.returned == s.amount


async def test_review_scheduler_versus_move_returns_once(
    login_wm, provisioned, litellm, prov_settings, fake_clock, ro_engine, monkeypatch
):
    from app.litellm_client import LiteLLMReader

    gid = await new_grant(login_wm)
    r = await share(await login_wm("dana"), gid, body(days=1))
    sid = int(r.json()["entities"][0]["id"])
    await drain(provisioned, litellm, prov_settings)
    fake_clock.advance(days=2)
    reading, release, moving_lock = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = WriteContext.lock

    async def observed_lock(ctx, kind, ident):
        if ctx.actor_upn == "rep@corp.example" and kind == LockKind.GROUP:
            moving_lock.set()
        await original(ctx, kind, ident)

    class PausedClient:
        async def team_info(self, team_id):
            reading.set()
            await release.wait()
            return await litellm.team_info(team_id)

    monkeypatch.setattr(WriteContext, "lock", observed_lock)
    expiring = asyncio.create_task(
        _expire_share(
            provisioned,
            prov_settings,
            sid,
            1,
            fake_clock.now(),
            PausedClient(),
            LiteLLMReader(ro_engine),
        )
    )
    await reading.wait()
    rep = await login_wm("rep")
    moving = asyncio.create_task(act(rep, 1, DANI, "move", {"to_group_id": 2}))
    await moving_lock.wait()
    release.set()
    _, moved = await asyncio.gather(expiring, moving)
    # Expiry's committed member update can make the move wait for outbox application.
    assert moved.status_code in (200, 409)
    async with provisioned() as db:
        g, s = await db.get(Grant, gid), await db.get(GrantShare, sid)
        assert g.returned == s.returned == 3000
        assert s.used + s.returned == s.amount


async def test_review_create_cannot_race_move_without_shares(
    login_wm, provisioned, litellm, prov_settings, monkeypatch
):
    gid = await new_grant(login_wm)
    await drain(provisioned, litellm, prov_settings)
    reached, resume = asyncio.Event(), asyncio.Event()
    original = lifecycle.close_member_shares

    async def paused(*args, **kwargs):
        await original(*args, **kwargs)
        reached.set()
        await resume.wait()

    monkeypatch.setattr(lifecycle, "close_member_shares", paused)
    rep, head = await login_wm("rep"), await login_wm("dana")
    moving = asyncio.create_task(act(rep, 1, DANI, "move", {"to_group_id": 2}))
    await reached.wait()
    creating = asyncio.create_task(share(head, gid, body()))
    try:
        async with provisioned() as observer:
            acquired = await observer.scalar(
                text("SELECT pg_try_advisory_xact_lock(:k, :i)"), {"k": int(LockKind.GROUP), "i": 1}
            )
            assert not acquired, "the membership transaction must hold the share creation lock"
    finally:
        resume.set()
        moved, created = await asyncio.gather(moving, creating)
    assert moved.status_code == 200
    assert created.status_code == 422 and created.json()["code"] == "SCOPE_FORBIDDEN"
