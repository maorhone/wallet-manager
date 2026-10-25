"""Step 5.5: internal systems are funded from the department envelope (D-076)."""

import uuid

import pytest
from sqlalchemy import select

from app.models import ProductionSystem

D1 = 1
pytestmark = pytest.mark.usefixtures("wide_envelope")


async def send(client, method, path, body, reason="internal systems"):
    headers = {
        "X-CSRF-Token": client.cookies["wm_csrf"],
        "X-Change-Reason": reason,
        "Idempotency-Key": uuid.uuid4().hex,
    }
    return await client.request(method, path, json=body, headers=headers)


async def unallocated(client):
    r = await client.get(f"/departments/{D1}/summary")
    assert r.status_code == 200, r.text
    return r.json()["wallet"]["unallocated"]


async def make_org(admin, budget=100000, *, free_budget=True):
    if free_budget:
        r = await send(
            admin, "PUT", "/departments/1/budget", {"budget": 1100000, "budget_period": "1mo"}
        )
        assert r.status_code == 200, r.text
    r = await send(admin, "PUT", f"/production/departments/{D1}", {"budget": budget})
    assert r.status_code == 200, r.text


async def test_internal_system_budget_reduces_department_allocatable(login_wm, provisioned):
    admin = await login_wm("admin")
    await make_org(admin)
    before = await unallocated(admin)
    r = await send(
        admin, "POST", f"/production/departments/{D1}/systems", {"name": "S", "budget": 60000}
    )
    assert r.status_code == 200, r.text
    assert await unallocated(admin) == before - 60000
    async with provisioned() as s:
        sysrow = (await s.scalars(select(ProductionSystem))).one()
        assert sysrow.funding == "department"


async def test_managing_section_can_edit_budget_within_delegation(login_wm, provisioned):
    admin = await login_wm("admin")
    await make_org(admin)
    r = await send(
        admin,
        "POST",
        f"/production/departments/{D1}/systems",
        {"name": "S", "budget": 10000, "managing_group_id": 1},
    )
    assert r.status_code == 200, r.text
    sys_id = int(r.json()["entities"][0]["id"])
    head = await login_wm("dana")  # head of group 1
    ok = await send(head, "PATCH", f"/production/systems/{sys_id}", {"budget": 20000})
    assert ok.status_code == 200, ok.text
    over = await send(head, "PATCH", f"/production/systems/{sys_id}", {"budget": 10**7})
    assert over.status_code == 422, over.text
    other = await login_wm("dani")  # not the managing section's head
    denied = await send(other, "PATCH", f"/production/systems/{sys_id}", {"budget": 15000})
    assert denied.status_code == 403, denied.text
    async with provisioned() as s:
        assert (await s.get(ProductionSystem, sys_id)).budget == 20000


async def test_legacy_production_endpoints_still_pass(login_wm, provisioned):
    admin = await login_wm("admin")
    await make_org(admin, 50000)
    r = await send(
        admin, "POST", f"/production/departments/{D1}/systems", {"name": "S", "budget": 30000}
    )
    assert r.status_code == 200, r.text
    got = (await admin.get(f"/production/departments/{D1}")).json()
    assert got["org"]["budget"] == 30000
    assert [s["budget"] for s in got["systems"]] == [30000]


async def test_internal_system_cannot_exceed_department_envelope(login_wm, provisioned):
    admin = await login_wm("admin")
    await make_org(admin, free_budget=False)
    r = await send(
        admin, "POST", "/production/departments/1/systems", {"name": "excess", "budget": 1}
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "PARENT_OVERALLOCATED"


async def test_department_usage_includes_only_internal_system_spend(
    login_wm, provisioned, snapshot
):
    from app.models import Department

    admin = await login_wm("admin")
    async with provisioned() as db:
        dept = await db.get(Department, 1)
        dept.budget += 1000
        db.add_all(
            [
                ProductionSystem(
                    department_id=1,
                    name="internal",
                    budget=100,
                    litellm_team_id="internal-spend",
                    funding="department",
                ),
                ProductionSystem(
                    organization_id=1,
                    name="org",
                    budget=100,
                    litellm_team_id="org-spend",
                    funding="organization",
                    responsible_department_id=1,
                ),
            ]
        )
        await db.commit()
        systems = list(await db.scalars(select(ProductionSystem).order_by(ProductionSystem.id)))
    await snapshot(
        [
            ("department", 1, None, 20),
            ("system", systems[0].id, None, 30),
            ("system", systems[0].id, "model", 30),
            ("system", systems[1].id, None, 90),
        ]
    )
    wallet = (await admin.get("/departments/1/summary")).json()["wallet"]
    assert wallet["used"] == 50
    assert wallet["remaining"] == wallet["total"] - 50


async def test_system_budget_reduction_preserves_keys_and_consumed_credit(
    login_wm, provisioned, snapshot
):
    from app.models import ServiceKey

    admin = await login_wm("admin")
    await make_org(admin)
    r = await send(
        admin, "POST", "/production/departments/1/systems", {"name": "S", "budget": 10000}
    )
    system_id = int(r.json()["entities"][0]["id"])
    async with provisioned() as db:
        active = ServiceKey(
            owner_kind="system", owner_id=system_id, max_budget=6000, status="active"
        )
        db.add_all(
            [
                active,
                ServiceKey(
                    owner_kind="system", owner_id=system_id, max_budget=2000, status="pending"
                ),
            ]
        )
        await db.commit()
        active_id = active.id
    # 1000 used by the active key plus 1000 consumed by a revoked key: 9000 committed.
    await snapshot([("system", system_id, None, 2000), ("service_key", active_id, None, 1000)])
    denied = await send(admin, "PATCH", f"/production/systems/{system_id}", {"budget": 8999})
    assert denied.status_code == 422, denied.text
    assert denied.json()["code"] == "PARENT_OVERALLOCATED"
    allowed = await send(admin, "PATCH", f"/production/systems/{system_id}", {"budget": 9000})
    assert allowed.status_code == 200, allowed.text
    async with provisioned() as db:
        assert (await db.get(ProductionSystem, system_id)).budget == 9000
    org = (await admin.get("/production/departments/1")).json()["org"]
    assert org["budget"] == 9000


async def test_system_budget_reduction_preserves_consumption_after_key_revocation(
    login_wm, provisioned, snapshot
):
    admin = await login_wm("admin")
    await make_org(admin)
    r = await send(
        admin, "POST", "/production/departments/1/systems", {"name": "S", "budget": 10000}
    )
    system_id = int(r.json()["entities"][0]["id"])
    await snapshot([("system", system_id, None, 8000)])
    denied = await send(admin, "PATCH", f"/production/systems/{system_id}", {"budget": 7999})
    assert denied.status_code == 422, denied.text
    allowed = await send(admin, "PATCH", f"/production/systems/{system_id}", {"budget": 8000})
    assert allowed.status_code == 200, allowed.text


async def test_system_growth_respects_department_envelope(login_wm, provisioned):
    admin = await login_wm("admin")
    await make_org(admin)
    r = await send(
        admin, "POST", "/production/departments/1/systems", {"name": "S", "budget": 10000}
    )
    system_id = int(r.json()["entities"][0]["id"])
    denied = await send(admin, "PATCH", f"/production/systems/{system_id}", {"budget": 100001})
    assert denied.status_code == 422, denied.text
    assert denied.json()["code"] == "PARENT_OVERALLOCATED"
