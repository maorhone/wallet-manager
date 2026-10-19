import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import LockKind, lock_parent


async def test_advisory_lock_serializes_two_sessions(engine):
    maker = async_sessionmaker(engine, expire_on_commit=False)
    got_second = asyncio.Event()

    async with maker() as s1, maker() as s2:
        await lock_parent(s1, LockKind.DEPARTMENT, 7)

        async def second():
            await lock_parent(s2, LockKind.DEPARTMENT, 7)
            got_second.set()

        task = asyncio.create_task(second())
        await asyncio.sleep(0.3)
        assert not got_second.is_set()
        await s1.commit()
        await asyncio.wait_for(task, 5)
        assert got_second.is_set()
        await s2.commit()


async def test_different_ids_do_not_block(engine):
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s1, maker() as s2:
        await lock_parent(s1, LockKind.GROUP, 1)
        await asyncio.wait_for(lock_parent(s2, LockKind.GROUP, 2), 2)


async def test_healthz_readyz(api):
    assert (await api.get("/healthz")).json() == {"status": "ok"}
    assert (await api.get("/readyz")).status_code == 200
