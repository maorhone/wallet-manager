from .helpers import assert_blocked, seed_team


async def test_a2_litellm_blocks_at_team_cap(target):
    keys = await seed_team(
        target,
        team_budget=100,
        members={"u1": {"budget": 60}, "u2": {"budget": 60}},
    )
    for uid, n in (("u1", 60), ("u2", 40)):
        for _ in range(n):
            assert (await target.chat(keys[uid])).status_code == 200
    assert_blocked(await target.chat(keys["u2"]), "team", target)
    over = await target.reader.overallocated_teams()
    assert [t.team_id for t in over] == ["team-c"]
