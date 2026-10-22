"""Phase 5.2: scoped hierarchy writes, inherited management and flat LiteLLM teams."""

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Department, Grp, LitellmWrite, Organization
from app.outbox.worker import drain


async def write(client, path, body, *, method="POST", key=None, reason="hierarchy change"):
    headers = {"X-CSRF-Token": client.cookies["wm_csrf"]}
    if reason is not None:
        headers["X-Change-Reason"] = reason
    if key is not None:
        headers["Idempotency-Key"] = key
    return await client.request(method, path, json=body, headers=headers)


def group_body(name, parent=None, department=1):
    return {
        "name": name,
        "litellm_team_id": name,
        "department_id": department,
        "parent_group_id": parent,
    }


async def create_group(client, name, parent=None, department=1):
    response = await write(client, "/groups", group_body(name, parent, department))
    assert response.status_code == 200, response.text
    return int(response.json()["entities"][0]["id"])


async def ids(factory):
    async with factory() as db:
        return dict((await db.execute(select(Grp.litellm_team_id, Grp.id))).all())


async def counts(factory):
    async with factory() as db:
        return tuple(
            [
                await db.scalar(select(func.count()).select_from(model))
                for model in (Organization, Department, Grp, AuditLog, LitellmWrite)
            ]
        )


async def test_admin_creates_org(login, seeded):
    admin = await login("admin")
    body = {
        "name": "Organization Two",
        "departments_envelope": "1234.56",
        "systems_envelope": "78.90",
        "budget_period": "1mo",
    }
    before = await counts(seeded)
    response = await write(admin, "/organizations", body, key="new-org")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "applied"
    assert response.headers["X-Request-ID"] == result["request_id"]
    entity = result["entities"][0]
    assert entity["entity"] == "organization"
    org_id = int(entity["id"])
    async with seeded() as db:
        org = await db.get(Organization, org_id)
        assert org.name == body["name"]
        assert org.departments_envelope == Decimal("1234.56")
        assert org.systems_envelope == Decimal("78.90")
        assert org.systems_litellm_org_id is None
        audit = await db.scalar(select(AuditLog).where(AuditLog.request_id == result["request_id"]))
        assert (audit.actor_upn, audit.actor_role, audit.reason) == (
            "admin@corp.example",
            "platform_admin",
            "hierarchy change",
        )
        assert audit.before == {}
        assert audit.after["departments_envelope"] == 1234.56
    after = await counts(seeded)
    assert after == (before[0] + 1, before[1], before[2], before[3] + 1, before[4])
    replay = await write(admin, "/organizations", body, key="new-org")
    assert replay.json() == result
    assert await counts(seeded) == after
    response = await write(
        admin,
        "/departments",
        {
            "name": "New Department",
            "litellm_org_id": "new-dept",
            "budget": 0,
            "organization_id": org_id,
        },
    )
    assert response.status_code == 200, response.text
    async with seeded() as db:
        dept = await db.get(Department, int(response.json()["entities"][0]["id"]))
        assert dept.organization_id == org_id


async def test_dept_head_adds_group_above_and_below(login_wm, provisioned, litellm, prov_settings):
    rep = await login_wm("rep")
    groups = await ids(provisioned)
    child = await create_group(rep, "nested-child", groups["A1"])
    leaf = await create_group(rep, "nested-leaf", child)
    response = await write(
        rep,
        f"/groups/{child}/insert-parent",
        {"name": "Middle", "litellm_team_id": "nested-middle"},
        key="insert-middle",
    )
    assert response.status_code == 200, response.text
    entities = response.json()["entities"]
    parent = int(next(e["id"] for e in entities if int(e["id"]) != child))
    async with provisioned() as db:
        assert (await db.get(Grp, parent)).parent_group_id == groups["A1"]
        assert (await db.get(Grp, child)).parent_group_id == parent
        assert (await db.get(Grp, leaf)).parent_group_id == child
        assert (await db.get(Grp, parent)).base_budget == 0
        audits = list(
            await db.scalars(
                select(AuditLog).where(AuditLog.request_id == response.json()["request_id"])
            )
        )
        assert len(audits) == 1
        assert audits[0].actor_role == "department_rep"
        assert audits[0].before["child_parent_group_id"] == groups["A1"]
        assert audits[0].after["child_id"] == child
    before_replay = await counts(provisioned)
    replay = await write(
        rep,
        f"/groups/{child}/insert-parent",
        {"name": "Middle", "litellm_team_id": "nested-middle"},
        key="insert-middle",
    )
    assert replay.json() == response.json()
    assert await counts(provisioned) == before_replay
    await drain(provisioned, litellm, prov_settings)
    team = (await litellm.team_info("nested-middle")).team_info
    assert (team.organization_id, team.max_budget) == ("D1", 0)


async def test_group_head_adds_subgroup_only_under_own(login, seeded):
    head = await login("dana")
    groups = await ids(seeded)
    child = await create_group(head, "head-child", groups["A1"])
    await create_group(head, "head-grandchild", child)
    before = await counts(seeded)
    for parent, department in [(None, 1), (groups["A2"], 1), (groups["C1"], 2)]:
        response = await write(head, "/groups", group_body("forbidden-child", parent, department))
        assert response.status_code in (403, 404), response.text
    response = await write(
        head,
        f"/groups/{child}/insert-parent",
        {"name": "Forbidden", "litellm_team_id": "forbidden-parent"},
    )
    assert response.status_code == 403
    response = await write(
        head, f"/groups/{child}/parent", {"parent_group_id": None}, method="PATCH"
    )
    assert response.status_code == 403
    assert await counts(seeded) == before


async def test_ancestor_head_manages_descendant(login, seeded):
    rep, head, other_head = await login("rep"), await login("dana"), await login("noa")
    groups = await ids(seeded)
    child = await create_group(rep, "managed-child", groups["A1"])
    leaf = await create_group(rep, "managed-leaf", child)
    path = f"/groups/{leaf}/member-defaults"
    response = await write(head, path, {"cap": 12}, method="PUT")
    assert response.status_code == 200, response.text
    assert (await head.get(f"/groups/{leaf}/members")).status_code == 200
    assert (await other_head.get(f"/groups/{leaf}/members")).status_code == 403
    response = await write(
        rep, f"/groups/{child}/parent", {"parent_group_id": groups["B1"]}, method="PATCH"
    )
    assert response.status_code == 200, response.text
    assert (await head.get(f"/groups/{leaf}/members")).status_code == 404
    response = await write(head, path, {"cap": 13}, method="PUT")
    assert response.status_code == 404


async def test_move_subtree_audited(login_wm, provisioned):
    rep = await login_wm("rep")
    groups = await ids(provisioned)
    child = await create_group(rep, "moving-child", groups["A1"])
    leaf = await create_group(rep, "moving-leaf", child)
    before = await counts(provisioned)
    response = await write(
        rep,
        f"/groups/{child}/parent",
        {"parent_group_id": groups["B1"]},
        method="PATCH",
        key="move-subtree",
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["entities"][0]["values"]["parent_group_id"] == groups["B1"]
    async with provisioned() as db:
        moved, descendant = await db.get(Grp, child), await db.get(Grp, leaf)
        assert moved.parent_group_id == groups["B1"]
        assert moved.litellm_team_id == "moving-child"
        assert descendant.parent_group_id == child
        audit = await db.scalar(select(AuditLog).where(AuditLog.request_id == result["request_id"]))
        assert audit.before["parent_group_id"] == groups["A1"]
        assert audit.after["parent_group_id"] == groups["B1"]
        assert audit.actor_role == "department_rep"
    after = await counts(provisioned)
    assert after == (*before[:3], before[3] + 1, before[4])
    assert (
        await write(
            rep,
            f"/groups/{child}/parent",
            {"parent_group_id": groups["B1"]},
            method="PATCH",
            key="move-subtree",
        )
    ).json() == result
    assert await counts(provisioned) == after
    response = await write(
        rep, f"/groups/{child}/parent", {"parent_group_id": None}, method="PATCH"
    )
    assert response.status_code == 200, response.text
    async with provisioned() as db:
        assert (await db.get(Grp, child)).parent_group_id is None


@pytest.mark.parametrize("who", ["rep", "dana", "pmo", "viewer", "dani"])
async def test_non_admin_cannot_create_org_403(login, seeded, who):
    client = await login(who)
    before = await counts(seeded)
    response = await write(client, "/organizations", {"name": "Forbidden"})
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "FORBIDDEN"
    assert await counts(seeded) == before


@pytest.mark.parametrize(
    "operation",
    [
        "self",
        "cycle",
        "move-depth",
        "insert-depth",
        "create-depth",
        "cross-move",
        "cross-create",
        "missing-parent",
        "missing-group",
    ],
)
async def test_invalid_hierarchy_is_atomic(login, seeded, operation):
    rep = await login("rep")
    groups = await ids(seeded)
    child = await create_group(rep, "constraint-child", groups["A1"])
    leaf = await create_group(rep, "constraint-leaf", child)
    bottom = await create_group(rep, "constraint-bottom", leaf)
    before = await counts(seeded)
    if operation == "insert-depth":
        response = await write(
            rep,
            f"/groups/{child}/insert-parent",
            {"name": "Too deep", "litellm_team_id": "too-deep"},
        )
    elif operation in ("cross-create", "create-depth"):
        response = await write(
            rep,
            "/groups",
            group_body("invalid-child", groups["C1"] if operation == "cross-create" else bottom),
        )
    else:
        parent = {
            "self": child,
            "cycle": bottom,
            "move-depth": groups["A1"],
            "cross-move": groups["C1"],
            "missing-parent": 99999,
            "missing-group": None,
        }[operation]
        # Moving a second root (height 4) below A1 would make its deepest node level 5.
        target = groups["A1"] if operation == "move-depth" else child
        if operation == "move-depth":
            parent = groups["B1"]
        if operation == "missing-group":
            target = 99999
        response = await write(
            rep, f"/groups/{target}/parent", {"parent_group_id": parent}, method="PATCH"
        )
    assert response.status_code == (404 if operation.startswith("missing") else 422), response.text
    assert response.json()["code"] == (
        "NOT_FOUND"
        if operation.startswith("missing")
        else "SCOPE_FORBIDDEN"
        if operation.startswith("cross")
        else "INVALID_REQUEST"
    )
    assert await counts(seeded) == before
    async with seeded() as db:
        assert (await db.get(Grp, child)).parent_group_id == groups["A1"]
        assert (await db.get(Grp, bottom)).parent_group_id == leaf


async def test_department_scope_and_write_contract(login, seeded):
    rep, admin = await login("rep"), await login("admin")
    groups = await ids(seeded)
    before = await counts(seeded)
    response = await write(
        rep, f"/groups/{groups['C1']}/parent", {"parent_group_id": None}, method="PATCH"
    )
    assert response.status_code == 404
    response = await write(admin, "/organizations", {"name": "No reason"}, reason=None)
    assert response.status_code == 422
    response = await admin.post(
        "/organizations", json={"name": "No csrf"}, headers={"X-Change-Reason": "test"}
    )
    assert response.status_code == 403
    response = await write(
        admin,
        "/departments",
        {
            "name": "Unknown root",
            "litellm_org_id": "unknown-root",
            "budget": 0,
            "organization_id": 99999,
        },
    )
    assert response.status_code == 404
    response = await write(admin, "/organizations", {"name": "Negative", "systems_envelope": -1})
    assert response.status_code == 422
    assert await counts(seeded) == before


async def test_org_allocator_manages_only_own_tree(login, seeded):
    admin = await login("admin")
    # Give a plain seeded employee the new role through the public binding API.
    async with seeded() as db:
        group = await db.scalar(select(Grp).where(Grp.litellm_team_id == "B1"))
        dn = group.ad_group_dn
    response = await write(
        admin,
        "/role-bindings",
        {
            "scope_kind": "organization",
            "scope_id": 1,
            "role": "org_allocator",
            "ad_group_dns": [dn],
        },
        method="PUT",
    )
    assert response.status_code == 200, response.text
    # The fixture user in B1 is a plain member, not a department representative.
    from app.directory import FixtureDirectory

    directory = FixtureDirectory("seed/dev.yaml")
    upn = directory.members_of(dn)[0]
    allocator = await login(upn.split("@")[0])
    from app.permissions import Binding, Principal, inherit_management
    from app.services.overallocation import visible_departments

    principal = Principal(upn, "Allocator", [Binding("org_allocator", "organization", 1)])
    async with seeded() as db:
        await inherit_management(db, principal)
    assert visible_departments(principal) == {1, 2}
    child = await create_group(allocator, "allocator-child")
    response = await write(
        allocator,
        f"/groups/{child}/insert-parent",
        {"name": "Allocator parent", "litellm_team_id": "allocator-parent"},
    )
    assert response.status_code == 200, response.text
    async with seeded() as db:
        audit = await db.scalar(
            select(AuditLog).where(AuditLog.request_id == response.json()["request_id"])
        )
        assert audit.actor_role == "org_allocator"
    response = await write(admin, "/organizations", {"name": "Other root"})
    org_id = int(response.json()["entities"][0]["id"])
    response = await write(
        admin,
        "/departments",
        {
            "name": "Other department",
            "litellm_org_id": "other-dept",
            "budget": 0,
            "organization_id": org_id,
        },
    )
    department = int(response.json()["entities"][0]["id"])
    async with seeded() as db:
        await inherit_management(db, principal)
    assert visible_departments(principal) == {1, 2}
    response = await write(allocator, "/groups", group_body("out-of-scope", department=department))
    assert response.status_code == 404
    assert (await write(allocator, "/organizations", {"name": "Forbidden"})).status_code == 403


async def test_concurrent_moves_cannot_form_cycle(login, seeded):
    rep = await login("rep")
    a = await create_group(rep, "concurrent-a")
    b = await create_group(rep, "concurrent-b")
    before = await counts(seeded)
    responses = await asyncio.gather(
        write(rep, f"/groups/{a}/parent", {"parent_group_id": b}, method="PATCH"),
        write(rep, f"/groups/{b}/parent", {"parent_group_id": a}, method="PATCH"),
    )
    assert sorted(r.status_code for r in responses) == [200, 422]
    after = await counts(seeded)
    assert after == (*before[:3], before[3] + 1, before[4])


async def test_inherited_write_rechecks_scope_after_concurrent_move(login, seeded, monkeypatch):
    from app import permissions
    from app.db import LockKind, lock_parent

    rep, head = await login("rep"), await login("dana")
    groups = await ids(seeded)
    child = await create_group(rep, "rechecked-child", groups["A1"])
    before = await counts(seeded)
    entered = asyncio.Event()
    original_lock = permissions.lock_parent

    async def observed_lock(*args):
        entered.set()
        return await original_lock(*args)

    monkeypatch.setattr(permissions, "lock_parent", observed_lock)
    async with seeded() as db:
        await lock_parent(db, LockKind.DEPARTMENT, 1)
        task = asyncio.create_task(
            write(head, f"/groups/{child}/member-defaults", {"cap": 15}, method="PUT")
        )
        try:
            await asyncio.wait_for(entered.wait(), 5)
            group = await db.get(Grp, child)
            group.parent_group_id = groups["B1"]
            await db.commit()
            response = await asyncio.wait_for(task, 5)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    assert response.status_code == 404, response.text
    assert await counts(seeded) == before
    async with seeded() as db:
        assert (await db.get(Grp, child)).default_member_cap == 0
