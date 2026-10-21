import asyncio

from sqlalchemy import func, select

from app.models import ProductionSystem

D1 = 1


async def post(client, path, body, reason="production", **headers):
    h = {"X-CSRF-Token": client.cookies["wm_csrf"], **headers}
    if reason is not None:
        h["X-Change-Reason"] = reason
    return await client.post(path, json=body, headers=h)


async def test_concurrent_systems_one_parent_overallocated(login_wm, provisioned, put, monkeypatch):
    admin = await login_wm("admin")
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
