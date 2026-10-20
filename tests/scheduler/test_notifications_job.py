"""SPEC §7.9 FR-N1: threshold notifications from the scheduler job (fake clock, no sleeping)."""

from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select

from app.directory import FixtureDirectory
from app.models import Grant, LitellmWrite, Notification, SpendSnapshot
from app.scheduler.jobs import run_notifications

DANI, DANA, PMO, ADMIN = (f"{u}@corp.example" for u in ("dani", "dana", "pmo", "admin"))


@pytest.fixture
def run(seeded, settings, fake_clock):
    async def _run():
        res = await run_notifications(
            seeded, None, fake_clock, settings=settings,
            directory=FixtureDirectory(settings.seed_file),
        )  # fmt: skip
        return res.created

    return _run


@pytest.fixture
def spend(seeded, settings, fake_clock):
    async def _spend(kind, scope_id, amount):
        period = fake_clock.now().astimezone(ZoneInfo(settings.app_timezone)).strftime("%Y-%m")
        async with seeded() as s:
            scope = (SpendSnapshot.scope_kind == kind, SpendSnapshot.scope_id == str(scope_id))
            await s.execute(delete(SpendSnapshot).where(*scope))
            s.add(
                SpendSnapshot(
                    scope_kind=kind,
                    scope_id=str(scope_id),
                    period=period,
                    model=None,
                    spend=Decimal(amount),
                )
            )
            await s.commit()

    return _spend


async def rows(factory, kind=None):
    async with factory() as s:
        q = select(Notification).order_by(Notification.id)
        if kind:
            q = q.where(Notification.kind == kind)
        return list((await s.scalars(q)).all())


async def test_employee_80_and_100(seeded, run, spend):
    assert await run() == {}
    await spend("employee", DANI, 12000)  # dani's cap is 15,000
    assert await run() == {"employee_80": 1}
    n = (await rows(seeded))[0]
    assert (n.upn, n.kind, n.scope_kind, n.scope_id) == (DANI, "employee_80", "group", 1)
    assert n.payload["percent"] == 0.8 and n.payload["key"].endswith(":80")
    await spend("employee", DANI, 15000)
    assert await run() == {"employee_80": 0, "employee_100": 1}
    assert {n.kind for n in await rows(seeded)} == {"employee_80", "employee_100"}


async def test_group_80_and_100_to_heads(seeded, run, spend):
    await spend("group", 1, 200000)  # A1 base budget is 250,000
    assert (await run())["group_80"] == 1
    assert [(n.upn, n.kind) for n in await rows(seeded)] == [(DANA, "group_80")]
    await spend("group", 1, 250000)
    assert (await run())["group_100"] == 1
    assert {(n.upn, n.kind) for n in await rows(seeded)} == {
        (DANA, "group_80"),
        (DANA, "group_100"),
    }


async def grant(factory, clock, days, amount=10000):
    now = clock.now()
    async with factory() as s:
        g = Grant(
            department_id=1, group_id=1, amount=amount, project_ref="PRJ-1", status="active",
            starts_at=now - timedelta(days=1), expires_at=now + timedelta(days=days),
        )  # fmt: skip
        s.add(g)
        await s.commit()
        return g.id


async def test_grant_expiring_7_days(seeded, run, fake_clock):
    await grant(seeded, fake_clock, 20)
    assert await run() == {}
    fake_clock.advance(days=14)  # 6 days left
    res = await run()
    assert res["grant_expiring"] == 2
    assert {n.upn for n in await rows(seeded, "grant_expiring")} == {PMO, DANA}


async def test_grant_underused(seeded, run, fake_clock):
    await grant(seeded, fake_clock, 5)
    res = await run()
    assert (res["grant_expiring"], res["grant_underused"]) == (2, 2)
    assert {n.upn for n in await rows(seeded, "grant_underused")} == {PMO, DANA}
    other = await grant(seeded, fake_clock, 30)  # not within 7 days: neither kind
    assert (await run()) == {"grant_expiring": 0, "grant_underused": 0}
    assert all(n.scope_id != other for n in await rows(seeded))


async def test_outbox_failed_to_admins(seeded, run):
    async with seeded() as s:
        s.add(LitellmWrite(entity="group", entity_id="1", endpoint="/team/update", status="failed",
                           last_error="boom"))  # fmt: skip
        s.add(LitellmWrite(entity="group", entity_id="2", endpoint="/team/update"))
        await s.commit()
    assert await run() == {"outbox_failed": 1}
    n = (await rows(seeded))[0]
    assert (n.upn, n.kind, n.payload["endpoint"]) == (ADMIN, "outbox_failed", "/team/update")


async def test_run_twice_no_duplicates(seeded, run, spend, fake_clock):
    await spend("employee", DANI, 15000)
    await grant(seeded, fake_clock, 3)
    first = await run()
    assert sum(first.values()) > 0
    count = len(await rows(seeded))
    assert sum((await run()).values()) == 0
    assert len(await rows(seeded)) == count
