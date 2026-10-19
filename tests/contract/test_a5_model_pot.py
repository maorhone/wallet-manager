from .helpers import assert_blocked, seed_team


async def _run(target, key, n):
    for _ in range(n):
        assert (await target.chat(key)).status_code == 200


async def test_a5_member_caps_within_pot(target):
    m = target.model
    keys = await seed_team(
        target,
        team_models={m: 10},
        members={"u1": {"models": {m: 4}}, "u2": {"models": {m: 4}}},
    )
    for uid in ("u1", "u2"):
        await _run(target, keys[uid], 4)
        assert_blocked(await target.chat(keys[uid]), "member_model", target)


async def test_a5_pot_binds_together(target):
    m = target.model
    keys = await seed_team(
        target,
        team_models={m: 10},
        members={"u1": {"models": {m: 6}}, "u2": {"models": {m: 6}}},
    )
    await _run(target, keys["u1"], 6)
    await _run(target, keys["u2"], 4)
    assert_blocked(await target.chat(keys["u2"]), "team_model", target)
