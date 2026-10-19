import asyncio

from sqlalchemy import func, select

from app.models import Grp


async def test_concurrent_splits_one_rejected(login, split, provisioned, monkeypatch):
    admin, rep = await login("admin"), await login("rep")
    # free 50000: each split below fits alone, both together overshoot D1's 1,000,000
    assert (await split(admin, [{"group_id": 1, "base": 200000}])).status_code == 200

    real_sleep = asyncio.sleep

    async def slow(delay, *a):  # widen the window between reading the sums and writing
        await real_sleep(max(delay, 0.3))

    monkeypatch.setattr("app.services.departments.asyncio.sleep", slow)
    a = split(admin, [{"group_id": 1, "base": 240000}])
    b = split(rep, [{"group_id": 2, "base": 190000}])
    ra, rb = await asyncio.gather(a, b)
    assert sorted([ra.status_code, rb.status_code]) == [200, 422]
    async with provisioned() as s:
        total = await s.scalar(select(func.sum(Grp.base_budget)).where(Grp.department_id == 1))
        assert total + 180000 + 50000 <= 1000000
