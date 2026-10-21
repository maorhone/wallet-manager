"""Step 4.3: daily report alerts, real read-only overlaps and fake time."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from prometheus_client import generate_latest
from sqlalchemy import select

from app import metrics
from app.directory import FixtureDirectory
from app.litellm_client import LiteLLMReader, Member, TeamMemberAdd, TeamMemberUpdate, TeamNew
from app.models import (
    Employee,
    EmployeeModelCap,
    GroupModelPot,
    Notification,
    NotificationPref,
    Policy,
    SpendSnapshot,
)
from app.outbox.desired import build
from app.scheduler import jobs
from app.scheduler.loop import build_jobs
from app.services.overallocation import scheduled_check

ADMIN, REP, DANI = (f"{u}@corp.example" for u in ("admin", "rep", "dani"))


async def rows(sf, kind):
    async with sf() as db:
        return list(await db.scalars(select(Notification).where(Notification.kind == kind)))


@pytest.fixture
async def report_run(provisioned, litellm, ro_engine, prov_settings, fake_clock):
    # 00:30 local on June 3, while UTC is still June 2.
    fake_clock.set(datetime(2026, 6, 2, 21, 30, tzinfo=UTC))

    async def run():
        return await jobs.run_report_alerts(
            provisioned,
            litellm,
            fake_clock,
            reader=LiteLLMReader(ro_engine),
            settings=prov_settings,
            directory=FixtureDirectory(prov_settings.seed_file),
        )

    return run


async def logs(fake_app):
    # Two overlapping requests at local June 2 midnight. June 1 and June 3 are excluded.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as c:
        response = await c.post(
            "/_test/spend/bulk",
            json=[
                dict(team_id="A1", user_id=DANI, model="m", amount=0, start=start, end=end)
                for start, end in [
                    ("2026-06-01T20:59:00Z", "2026-06-01T21:01:00Z"),
                    ("2026-06-01T21:00:00Z", "2026-06-01T21:02:00Z"),
                    ("2026-05-31T21:00:00Z", "2026-05-31T22:00:00Z"),
                    ("2026-06-02T21:00:00Z", "2026-06-02T22:00:00Z"),
                ]
            ],
        )
        assert response.status_code == 200, response.text


async def test_overallocation_alert_to_admins_and_rep_once_per_day(
    report_run, provisioned, litellm, fake_clock, prov_settings, ro_engine
):
    await litellm.team_member_update(
        TeamMemberUpdate(
            team_id="A1", user_id=DANI, max_budget_in_team=300000, budget_duration="1mo"
        )
    )
    await report_run()
    found = [n for n in await rows(provisioned, "overallocation") if n.payload["entity_id"] == 1]
    assert {n.upn for n in found} == {ADMIN, REP}
    assert all(n.payload["key"] == "overalloc:group:1:None:2026-06-03" for n in found)
    assert all(n.payload["source"] == "litellm" and n.payload["allocated"] == 330000 for n in found)
    assert all((n.scope_kind, n.scope_id) == ("group", 1) for n in found)
    # The worker's more frequent check shares the daily dedupe key with the scheduler.
    await scheduled_check(
        provisioned, LiteLLMReader(ro_engine), clock=fake_clock, settings=prov_settings
    )
    assert (
        len([n for n in await rows(provisioned, "overallocation") if n.payload["entity_id"] == 1])
        == 2
    )
    fake_clock.advance(days=1)
    await report_run()
    assert (
        len([n for n in await rows(provisioned, "overallocation") if n.payload["entity_id"] == 1])
        == 4
    )


async def test_concurrency_at_limit_alert_to_admins(report_run, provisioned, fake_app):
    await logs(fake_app)
    async with provisioned() as db:
        (await db.get(Employee, DANI)).max_parallel = 2
        db.add(EmployeeModelCap(upn=DANI, model="m", amount=5))
        # Exhaust both base and model caps; the gauge counts the employee once.
        db.add_all(
            [
                SpendSnapshot(
                    scope_kind="employee", scope_id=DANI, period="2026-06-01", model=m, spend=v
                )
                for m, v in [(None, 15000), ("m", 5)]
            ]
        )
        await db.commit()
    await report_run()
    [n] = await rows(provisioned, "concurrency_at_limit")
    assert n.upn == ADMIN and n.payload["day"] == "2026-06-02"
    assert n.payload["count"] == 1
    [m] = n.payload["members"]
    assert (m["upn"], m["peak"], m["max_parallel"]) == (DANI, 2, 2)
    assert m["at"] == "2026-06-01T21:00:00+00:00"
    output = generate_latest(metrics.REGISTRY).decode()
    assert 'wm_concurrency_peak{scope="global"} 2.0' in output
    assert "wm_blocked_employees 1.0" in output


@pytest.mark.parametrize(
    "policy_default,override,peak,at_limit",
    [
        (4, None, 4, True),
        (4, None, 3, False),
        (4, None, 5, True),
        (7, None, 7, True),
        (4, 6, 4, False),
        (None, None, 4, False),
    ],
)
async def test_concurrency_alert_uses_effective_key_limit(
    report_run, provisioned, fake_app, prov_settings, policy_default, override, peak, at_limit
):
    async with provisioned() as db:
        member = await db.get(Employee, DANI)
        member.max_parallel = override
        policy = await db.get(Policy, "concurrency")
        if policy_default is None:
            await db.delete(policy)
        else:
            policy.value = {**policy.value, "default": policy_default}
        await db.commit()
        key = await build(db, prov_settings, "/key/update", DANI)
        assert key.max_parallel_requests == (override or policy_default)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as c:
        response = await c.post(
            "/_test/spend/bulk",
            json=[
                dict(
                    team_id="A1",
                    user_id=DANI,
                    model="m",
                    amount=0,
                    start="2026-06-02T10:00:00Z",
                    end="2026-06-02T11:00:00Z",
                )
                for _ in range(peak)
            ],
        )
        assert response.status_code == 200, response.text
    await report_run()
    notifications = await rows(provisioned, "concurrency_at_limit")
    if not at_limit:
        assert notifications == []
        return
    [notification] = notifications
    assert notification.upn == ADMIN
    assert notification.payload["count"] == 1
    [member] = notification.payload["members"]
    assert (member["upn"], member["peak"], member["max_parallel"]) == (
        DANI,
        peak,
        key.max_parallel_requests,
    )


async def test_run_twice_no_duplicates(report_run, provisioned, fake_app):
    await logs(fake_app)
    async with provisioned() as db:
        (await db.get(Employee, DANI)).max_parallel = 2
        await db.commit()
    await report_run()
    first = {k: len(await rows(provisioned, k)) for k in ("overallocation", "concurrency_at_limit")}
    assert all(first.values())
    await report_run()
    assert first == {k: len(await rows(provisioned, k)) for k in first}


async def test_below_limit_and_muted_concurrency(report_run, provisioned, fake_app):
    await logs(fake_app)
    async with provisioned() as db:
        (await db.get(Employee, DANI)).max_parallel = 3
        await db.commit()
    await report_run()
    assert await rows(provisioned, "concurrency_at_limit") == []
    async with provisioned() as db:
        (await db.get(Employee, DANI)).max_parallel = 2
        db.add(NotificationPref(upn=ADMIN, kind="concurrency_at_limit", muted=True))
        await db.commit()
    await report_run()
    assert await rows(provisioned, "concurrency_at_limit") == []


async def test_empty_day_resets_gauges(report_run, provisioned, fake_app, fake_clock):
    await logs(fake_app)
    await report_run()
    fake_clock.advance(days=3)
    await report_run()
    output = generate_latest(metrics.REGISTRY).decode()
    assert 'wm_concurrency_peak{scope="global"} 0.0' in output
    assert "wm_blocked_employees 0.0" in output
    assert await rows(provisioned, "concurrency_at_limit") == []


async def test_model_breaches_have_separate_keys_and_department_scope(report_run, provisioned):
    async with provisioned() as db:
        db.add_all([GroupModelPot(group_id=1, model=m, amount=1) for m in ("m1", "m2")])
        await db.commit()
    await report_run()
    found = [
        n for n in await rows(provisioned, "overallocation") if n.payload["level"] == "group_model"
    ]
    assert len(found) == 4
    assert {n.payload["model"] for n in found} == {"m1", "m2"}
    assert all((n.scope_kind, n.scope_id) == ("department", 1) for n in found)
    assert {n.upn for n in found} == {ADMIN, REP}


async def test_report_job_registered_daily(provisioned, prov_settings, fake_clock):
    todo = build_jobs(prov_settings, provisioned, None, None, None, fake_clock)
    [job] = [j for j in todo if j.name == "report_alerts"]
    assert job.interval == timedelta(days=1) and job.run_at_start
    assert len({j.lock_id for j in todo}) == len(todo)


async def test_unknown_live_scope_alerts_admins_only(report_run, provisioned, litellm):
    await litellm.team_new(TeamNew(team_id="unmanaged-team", team_alias="Unmanaged", max_budget=1))
    await litellm.team_member_add(
        TeamMemberAdd(team_id="unmanaged-team", member=Member(user_id=DANI), max_budget_in_team=2)
    )
    await litellm.team_member_update(
        TeamMemberUpdate(
            team_id="unmanaged-team", user_id=DANI, max_budget_in_team=2, budget_duration="1mo"
        )
    )
    await report_run()
    [n] = [
        n
        for n in await rows(provisioned, "overallocation")
        if n.payload["entity_id"] == "unmanaged-team"
    ]
    assert n.upn == ADMIN and n.scope_kind is None and n.scope_id is None


async def test_count_before_top_100_and_exclude_left_unknown_members(
    report_run, provisioned, fake_app
):
    upns = [f"pilot-{i:03}@corp.example" for i in range(101)]
    async with provisioned() as db:
        db.add_all(Employee(upn=u, group_id=1, base_cap=1, max_parallel=1) for u in upns)
        db.add(
            Employee(upn="left@corp.example", group_id=1, base_cap=1, max_parallel=1, status="left")
        )
        await db.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as c:
        r = await c.post(
            "/_test/spend/bulk",
            json=[
                dict(
                    team_id="A1",
                    user_id=u,
                    model="m",
                    amount=0,
                    start="2026-06-02T10:00:00Z",
                    end="2026-06-02T11:00:00Z",
                )
                for u in [*upns, "left@corp.example", "unknown@corp.example"]
            ],
        )
        assert r.status_code == 200, r.text
    await report_run()
    [n] = await rows(provisioned, "concurrency_at_limit")
    assert n.upn == ADMIN and n.payload["count"] == 101
    assert [m["upn"] for m in n.payload["members"]] == upns[:100]
    assert 'wm_concurrency_peak{scope="global"} 103.0' in generate_latest(metrics.REGISTRY).decode()
