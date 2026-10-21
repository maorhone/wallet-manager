import asyncio

from app.litellm_client import ServiceAccountKeyGenerate

from .helpers import assert_blocked, seed_team


async def test_service_key_budget_parallel_and_models(target):
    await seed_team(target, team_budget=100)
    await target.configure(latency_ms=100, cost_per_request=1)
    gen = await target.client.key_service_account_generate(
        ServiceAccountKeyGenerate(
            team_id="team-c",
            max_budget=2,
            max_parallel_requests=1,
            models=[target.model],
        )
    )
    assert (await target.chat(gen.key, "disallowed-model")).status_code in (400, 403)
    responses = await asyncio.gather(*(target.chat(gen.key) for _ in range(2)))
    assert sorted(r.status_code for r in responses) == [200, 429]
    assert (await target.chat(gen.key)).status_code == 200
    assert_blocked(await target.chat(gen.key), "key", target)
