"""§11.1 POST /departments, POST /departments/{id}/groups; §9.3 New budget group; D13."""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import LockKind
from app.models import AuditLog, Department, Grp, LitellmWrite
from app.outbox.worker import drain
from app.writes import WriteContext

D1 = 1


async def post(client, path, body, reason="create", **headers):
    h = {"X-CSRF-Token": client.cookies["wm_csrf"], **headers}
    if reason is not None:
        h["X-Change-Reason"] = reason
    return await client.post(path, json=body, headers=h)


async def test_create_department_enqueues_organization_new(
    login_wm, provisioned, litellm, prov_settings
):
    admin = await login_wm("admin")
    body = {
        "name": "Department D3", "litellm_org_id": "D3",
        "ad_group_dn": "CN=dept-d3,OU=Groups,DC=corp,DC=example", "budget": 400000,
    }  # fmt: skip
    r = await post(admin, "/departments", body)
    assert r.status_code == 200, r.text
    ent = r.json()["entities"][0]
    assert (ent["entity"], ent["sync_status"]) == ("department", "pending")
    dept_id = int(ent["id"])

    await drain(provisioned, litellm, prov_settings)
    info = await litellm.organization_info("D3")
    assert info.organization_alias == "Department D3"
    assert info.litellm_budget_table.max_budget == 400000
    assert info.litellm_budget_table.budget_duration == "1mo"
    async with provisioned() as s:
        d = await s.get(Department, dept_id)
        assert (d.name, d.litellm_org_id, d.budget_period) == ("Department D3", "D3", "1mo")


async def test_create_group_team_new_zero_budget_after_drain(
    login_wm, provisioned, litellm, prov_settings
):
    admin = await login_wm("admin")
    body = {
        "name": "Group A3",
        "litellm_team_id": "A3",
        "ad_group_dn": "CN=grp-a3,OU=Groups,DC=corp,DC=example",
    }
    r = await post(admin, f"/departments/{D1}/groups", body)
    assert r.status_code == 200, r.text
    ent = r.json()["entities"][0]
    assert (ent["entity"], ent["sync_status"]) == ("group", "pending")
    assert ent["values"]["default_member_cap"] == 0
    group_id = int(ent["id"])

    await drain(provisioned, litellm, prov_settings)
    team = (await litellm.team_info("A3")).team_info
    assert (team.max_budget, team.organization_id) == (0, "D1")
    async with provisioned() as s:
        g = await s.get(Grp, group_id)
        assert (g.base_budget, g.status, g.kind) == (0, "active", "regular")


async def test_second_staff_group_invalid(login_wm, provisioned):
    admin = await login_wm("admin")
    body = {
        "name": "Another staff", "litellm_team_id": "d1-staff-2",
        "ad_group_dn": "CN=grp-d1-staff-2,OU=Groups,DC=corp,DC=example", "kind": "department_staff",
    }  # fmt: skip
    r = await post(admin, f"/departments/{D1}/groups", body)
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "INVALID_REQUEST"
    assert body["details"]["field"] == "kind"


async def test_duplicate_team_id_invalid(login_wm, provisioned):
    admin = await login_wm("admin")
    body = {
        "name": "Dup",
        "litellm_team_id": "A1",
        "ad_group_dn": "CN=dup,OU=Groups,DC=corp,DC=example",
    }
    r = await post(admin, f"/departments/{D1}/groups", body)
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "INVALID_REQUEST"
    assert body["details"]["field"] == "litellm_team_id"


async def test_department_org_id_reserved_prod_suffix_invalid(login_wm, provisioned):
    admin = await login_wm("admin")
    body = {"name": "Shadow", "litellm_org_id": "D1-prod", "budget": 0}
    r = await post(admin, "/departments", body)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "INVALID_REQUEST"
    assert body["details"]["field"] == "litellm_org_id"


async def test_group_team_id_reserved_sys_prefix_invalid(login_wm, provisioned):
    admin = await login_wm("admin")
    body = {"name": "Shadow", "litellm_team_id": "sys-1"}
    r = await post(admin, f"/departments/{D1}/groups", body)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "INVALID_REQUEST"
    assert body["details"]["field"] == "litellm_team_id"


async def test_non_admin_403(login_wm, provisioned):
    rep = await login_wm("rep")
    dept_body = {"name": "X", "litellm_org_id": "org-x", "budget": 1000}
    assert (await post(rep, "/departments", dept_body)).status_code == 403
    group_body = {"name": "X", "litellm_team_id": "team-x"}
    assert (await post(rep, f"/departments/{D1}/groups", group_body)).status_code == 403


@pytest.mark.parametrize("conflict", ["department_dn", "group_dn", "staff"])
async def test_concurrent_creation_conflicts_are_serialized(login, seeded, monkeypatch, conflict):
    admin = await login("admin")
    async with seeded() as s:
        dept = Department(name="New", litellm_org_id="new-dept", budget=0)
        s.add(dept)
        await s.commit()
        dept_id = dept.id
    path = "/departments" if conflict == "department_dn" else f"/departments/{dept_id}/groups"
    bodies = []
    for i in range(2):
        body = {"name": f"New {i}", "ad_group_dn": "CN=duplicate,DC=corp,DC=example"}
        if conflict == "department_dn":
            body.update(litellm_org_id=f"new-org-{i}", budget=0)
        else:
            body.update(litellm_team_id=f"new-team-{i}")
            if conflict == "staff":
                body.update(kind="department_staff", ad_group_dn=None)
        bodies.append(body)

    # Pause the first request after validation. Release it when the second either
    # attempts the shared lock or also reaches flush (the original race).
    entered, release = asyncio.Event(), asyncio.Event()
    locks_seen = []
    original_flush, original_lock = AsyncSession.flush, WriteContext.lock

    async def flush(session, *args, **kwargs):
        if any(isinstance(row, (Department, Grp)) for row in session.new):
            if not entered.is_set():
                entered.set()
                await release.wait()
            else:
                release.set()
        return await original_flush(session, *args, **kwargs)

    async def lock(ctx, kind, lock_id):
        locks_seen.append((kind, lock_id))
        if entered.is_set():
            release.set()
        return await original_lock(ctx, kind, lock_id)

    monkeypatch.setattr(AsyncSession, "flush", flush)
    monkeypatch.setattr(WriteContext, "lock", lock)
    first = asyncio.create_task(post(admin, path, bodies[0]))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        second_path = "/departments/2/groups" if conflict == "group_dn" else path
        results = await asyncio.wait_for(
            asyncio.gather(first, post(admin, second_path, bodies[1])), 10
        )
    finally:
        release.set()
        if not first.done():
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
    registry_id = 0 if conflict == "department_dn" else 1
    assert locks_seen.count((LockKind.PLATFORM, registry_id)) == 2
    if conflict != "department_dn":
        assert (LockKind.DEPARTMENT, dept_id) in locks_seen
    assert sorted(r.status_code for r in results) == [200, 422]
    rejected = next(r for r in results if r.status_code == 422)
    assert rejected.json()["code"] == "INVALID_REQUEST"
    assert rejected.json()["details"]["field"] == ("kind" if conflict == "staff" else "ad_group_dn")
    async with seeded() as s:
        model = Department if conflict == "department_dn" else Grp
        assert len(list(await s.scalars(select(model).where(model.name.like("New %"))))) == 1
        audits = list(await s.scalars(select(AuditLog).where(AuditLog.reason == "create")))
        assert len(audits) == 1
        assert len(list(await s.scalars(select(LitellmWrite)))) == 1


@pytest.mark.parametrize("conflict", ["department_dn", "group_dn", "staff"])
async def test_creation_constraints_translate_integrity_errors(
    login, seeded, monkeypatch, conflict
):
    admin = await login("admin")
    async with seeded() as s:
        if conflict == "department_dn":
            existing = await s.get(Department, D1)
            path = "/departments"
            body = {
                "name": "Dup",
                "litellm_org_id": "new-org",
                "budget": 0,
                "ad_group_dn": existing.ad_group_dn,
            }
        else:
            existing = await s.scalar(
                select(Grp).where(
                    Grp.department_id == D1,
                    Grp.kind == ("department_staff" if conflict == "staff" else "regular"),
                )
            )
            path = f"/departments/{D1}/groups"
            body = {"name": "Dup", "litellm_team_id": "new-team"}
            if conflict == "staff":
                body["kind"] = "department_staff"
            else:
                body["ad_group_dn"] = existing.ad_group_dn
        assert conflict == "staff" or body["ad_group_dn"]
        audit_count = len(list(await s.scalars(select(AuditLog))))

    # Bypass the friendly precheck to exercise the database backstop and its
    # error translation, as needed for writers outside the advisory-lock path.
    original_scalar = AsyncSession.scalar

    async def scalar(session, statement, *args, **kwargs):
        if str(statement).startswith(("SELECT department.id", "SELECT grp.id")):
            return None
        return await original_scalar(session, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "scalar", scalar)
    r = await post(admin, path, body)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "INVALID_REQUEST"
    assert r.json()["details"]["field"] == ("kind" if conflict == "staff" else "ad_group_dn")
    async with seeded() as s:
        assert len(list(await s.scalars(select(AuditLog)))) == audit_count
        assert not list(await s.scalars(select(LitellmWrite)))
