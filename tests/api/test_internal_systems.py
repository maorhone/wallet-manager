"""Step 5.5: internal systems are funded from the department envelope (D-076)."""

import uuid

from sqlalchemy import select

from app.models import ProductionSystem

D1 = 1


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


async def make_org(admin, budget=100000):
    r = await send(admin, "PUT", f"/production/departments/{D1}", {"budget": budget})
    assert r.status_code == 200, r.text


async def test_internal_system_budget_reduces_department_allocatable(login_wm, provisioned):
    admin = await login_wm("admin")
    before = await unallocated(admin)
    await make_org(admin)
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
    assert got["org"]["budget"] == 50000
    assert [s["budget"] for s in got["systems"]] == [30000]
