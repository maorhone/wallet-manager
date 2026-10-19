import asyncio

from .helpers import seed_team


async def test_a3_key_parallel_limit(target):
    await target.configure(latency_ms=500)
    keys = await seed_team(target, members={"u1": {"parallel": 3}})
    resps = await asyncio.gather(*(target.chat(keys["u1"]) for _ in range(6)))
    codes = sorted(r.status_code for r in resps)
    assert codes == [200, 200, 200, 429, 429, 429]
