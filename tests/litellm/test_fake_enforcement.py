from datetime import UTC, datetime, timedelta

import httpx

from app.litellm_client import JwtKeyMappingNew, TeamMemberUpdate
from fake_litellm.enforcement import make_test_jwt
from tests.contract.helpers import Target, assert_blocked, seed_team


async def _target(fake_app, litellm, ro_engine) -> Target:
    from app.litellm_client import LiteLLMReader

    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake_app), base_url="http://fake")
    return Target("fake", litellm, LiteLLMReader(ro_engine), http, "test-model")


async def test_unmapped_jwt_403(fake_app, litellm, ro_engine):
    t = await _target(fake_app, litellm, ro_engine)
    r = await t.chat(make_test_jwt("nobody@x.test"))
    assert r.status_code == 403


async def test_mapped_jwt_works_and_blocked_key_401(fake_app, litellm, ro_engine):
    t = await _target(fake_app, litellm, ro_engine)
    keys = await seed_team(t, members={"u1": {}})
    gen = await litellm.key_list("u1")
    await litellm.jwt_mapping_new(
        JwtKeyMappingNew(
            jwt_issuer="https://adfs.test",
            jwt_claim_name="upn",
            jwt_claim_value="u1@x.test",
            token=gen.keys[0].token,
        )
    )
    assert (await t.chat(make_test_jwt("u1@x.test"))).status_code == 200
    from app.litellm_client import KeyBlock

    await litellm.key_block(KeyBlock(key=keys["u1"]))
    assert (await t.chat(keys["u1"])).status_code == 401


async def test_member_cap_blocks(fake_app, litellm, ro_engine):
    t = await _target(fake_app, litellm, ro_engine)
    keys = await seed_team(t, members={"u1": {"budget": 2}})
    assert (await t.chat(keys["u1"])).status_code == 200
    assert (await t.chat(keys["u1"])).status_code == 200
    assert_blocked(await t.chat(keys["u1"]), "team_member", t)


async def test_temp_increase_counts_until_expiry(fake_app, litellm, ro_engine):
    t = await _target(fake_app, litellm, ro_engine)
    keys = await seed_team(t, members={"u1": {"budget": 1}})

    async def bump(expiry):
        await litellm.team_member_update(
            TeamMemberUpdate(
                team_id="team-c",
                user_id="u1",
                max_budget_in_team=1,
                budget_duration="1mo",
                temp_budget_increase=2,
                temp_budget_expiry=expiry,
            )
        )

    await bump(datetime.now(UTC) + timedelta(hours=1))
    for _ in range(3):
        assert (await t.chat(keys["u1"])).status_code == 200
    assert_blocked(await t.chat(keys["u1"]), "team_member", t)
    # expired increase no longer counts: limit is back to 1, spend is already 3
    await bump(datetime.now(UTC) - timedelta(hours=1))
    assert_blocked(await t.chat(keys["u1"]), "team_member", t)
