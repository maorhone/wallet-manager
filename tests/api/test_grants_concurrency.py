import asyncio

from grants import A1, A2, create, grant_body
from sqlalchemy import func, select

from app.models import Grant


async def test_a11_concurrent_grants_one_pool_insufficient(login, provisioned, monkeypatch):
    pmo, admin = await login("pmo"), await login("admin")
    real_sleep = asyncio.sleep

    async def slow(delay, *a):  # widen the window between reading the pool and writing the grant
        await real_sleep(max(delay, 0.3))

    monkeypatch.setattr("app.services.grants.asyncio.sleep", slow)
    # 100000 each fits the 180000 pool alone; both together do not
    a = create(pmo, grant_body(A1, 100000))
    b = create(admin, grant_body(A2, 100000))
    ra, rb = await asyncio.gather(a, b)
    assert sorted([ra.status_code, rb.status_code]) == [200, 422]
    assert {ra.json().get("code"), rb.json().get("code")} == {None, "PMO_POOL_INSUFFICIENT"}
    async with provisioned() as s:
        assert await s.scalar(select(func.sum(Grant.amount))) == 100000
