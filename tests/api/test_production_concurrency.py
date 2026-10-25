import asyncio

import pytest
from sqlalchemy import func, select

from app.models import ProductionSystem

D1 = 1
pytestmark = pytest.mark.usefixtures("wide_envelope")


async def post(client, path, body, reason="production", **headers):
    h = {"X-CSRF-Token": client.cookies["wm_csrf"], **headers}
    if reason is not None:
        h["X-Change-Reason"] = reason
    return await client.post(path, json=body, headers=h)


async def test_concurrent_systems_one_parent_overallocated(login_wm, provisioned, put, monkeypatch):
    admin = await login_wm("admin")
    r = await put(admin, "/departments/1/budget", {"budget": 1050000, "budget_period": "1mo"})
    assert r.status_code == 200, r.text
    await put(admin, f"/production/departments/{D1}", {"budget": 50000})

    real_sleep = asyncio.sleep

    async def slow(delay, *a):  # widen the window between reading the sum and writing the row
        await real_sleep(max(delay, 0.3))

    monkeypatch.setattr("app.services.production.asyncio.sleep", slow)
    # 30000 each fits the 50000 budget alone; both together do not
    a = post(admin, f"/production/departments/{D1}/systems", {"name": "Sys A", "budget": 30000})
    b = post(admin, f"/production/departments/{D1}/systems", {"name": "Sys B", "budget": 30000})
    ra, rb = await asyncio.gather(a, b)
    assert sorted([ra.status_code, rb.status_code]) == [200, 422]
    assert {ra.json().get("code"), rb.json().get("code")} == {None, "PARENT_OVERALLOCATED"}
    async with provisioned() as s:
        total = await s.scalar(
            select(func.sum(ProductionSystem.budget)).where(ProductionSystem.department_id == D1)
        )
        assert total == 30000


async def test_system_creation_serializes_with_department_split(login_wm, provisioned, put):
    admin = await login_wm("admin")
    assert (
        await put(admin, "/departments/1/budget", {"budget": 1050000, "budget_period": "1mo"})
    ).status_code == 200
    assert (await put(admin, "/production/departments/1", {"budget": 50000})).status_code == 200
    created, split = await asyncio.gather(
        post(admin, "/production/departments/1/systems", {"name": "S", "budget": 30000}),
        put(admin, "/departments/1/split", {"groups": [], "pmo_pool": 180000, "reserve": 80000}),
    )
    assert sorted([created.status_code, split.status_code]) == [200, 422]
    from app.models import Department, Grp

    async with provisioned() as db:
        dept = await db.get(Department, 1)
        bases = await db.scalar(
            select(func.sum(Grp.base_budget)).where(
                Grp.department_id == 1, Grp.parent_group_id.is_(None)
            )
        )
        systems = await db.scalar(
            select(func.coalesce(func.sum(ProductionSystem.budget), 0)).where(
                ProductionSystem.department_id == 1
            )
        )
        assert bases + dept.pmo_pool + dept.reserve + systems <= dept.budget
