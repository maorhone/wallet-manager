"""Fake LiteLLM lifecycle endpoints (SPEC §10 v0.3, V6/V7) and test controls."""

import httpx
import pytest

from app.litellm_client import (
    JwtKeyMappingNew,
    KeyBlock,
    KeyDelete,
    LiteLLMPermanentError,
    LiteLLMReader,
    LiteLLMTransientError,
    TeamMemberDelete,
    UserDelete,
)
from app.litellm_client.models import JwtKeyMappingUpdate
from fake_litellm.enforcement import JWT_ISSUER, make_test_jwt
from tests.contract.helpers import Target, seed_team


async def _target(fake_app, litellm, ro_engine) -> Target:
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake_app), base_url="http://fake")
    return Target("fake", litellm, LiteLLMReader(ro_engine), http, "test-model")


async def _mapped(t: Target, user: str = "u1") -> tuple[str, str]:
    """Seed a team with one member and map ``<user>@x.test`` to the member's key."""
    keys = await seed_team(t, members={user: {}})
    token = (await t.client.key_list(user)).keys[0].token
    await t.client.jwt_mapping_new(
        JwtKeyMappingNew(
            jwt_issuer=JWT_ISSUER,
            jwt_claim_name="upn",
            jwt_claim_value=f"{user}@x.test",
            token=token,
        )
    )
    return keys[user], token


async def test_key_delete_removes_key_and_mapping(fake_app, litellm, ro_engine):
    t = await _target(fake_app, litellm, ro_engine)
    key, token = await _mapped(t)
    assert (await t.chat(make_test_jwt("u1@x.test"))).status_code == 200
    await litellm.key_delete(KeyDelete(keys=[token]))
    assert (await litellm.key_list("u1")).keys == []
    assert (await t.chat(key)).status_code == 401
    assert (await t.chat(make_test_jwt("u1@x.test"))).status_code == 403  # mapping gone too
    with pytest.raises(LiteLLMPermanentError) as exc:
        await litellm.key_delete(KeyDelete(keys=[token]))
    assert exc.value.status == 404


async def test_user_delete(fake_app, litellm, ro_engine):
    t = await _target(fake_app, litellm, ro_engine)
    await seed_team(t, members={"u1": {}})
    await litellm.user_delete(UserDelete(user_ids=["u1"]))
    with pytest.raises(LiteLLMPermanentError) as exc:
        await litellm.user_delete(UserDelete(user_ids=["u1"]))
    assert exc.value.status == 404


async def test_member_delete_non_member_404(fake_app, litellm, ro_engine):
    t = await _target(fake_app, litellm, ro_engine)
    await seed_team(t, members={"u1": {}})
    await litellm.team_member_delete(TeamMemberDelete(team_id="team-c", user_id="u1"))
    with pytest.raises(LiteLLMPermanentError) as exc:
        await litellm.team_member_delete(TeamMemberDelete(team_id="team-c", user_id="u1"))
    assert exc.value.status == 404


async def test_inactive_mapping_rejects_jwt(fake_app, litellm, ro_engine):
    t = await _target(fake_app, litellm, ro_engine)
    await _mapped(t)
    jwt = make_test_jwt("u1@x.test")
    ident = dict(jwt_issuer=JWT_ISSUER, jwt_claim_name="upn", jwt_claim_value="u1@x.test")
    await litellm.jwt_mapping_update(JwtKeyMappingUpdate(**ident, is_active=False))
    assert (await t.chat(jwt)).status_code == 403  # like an unmapped upn (A10)
    await litellm.jwt_mapping_update(JwtKeyMappingUpdate(**ident, is_active=True))
    assert (await t.chat(jwt)).status_code == 200


async def test_fail_injection_then_ok(fake_app, litellm, ro_engine):
    t = await _target(fake_app, litellm, ro_engine)
    await seed_team(t, members={"u1": {}})
    r = await t.http.post("/_test/fail", json={"endpoint": "/key/block", "status": 500, "times": 1})
    assert r.status_code == 200
    token = (await litellm.key_list("u1")).keys[0].token
    with pytest.raises(LiteLLMTransientError):
        await litellm.key_block(KeyBlock(key=token))
    await litellm.key_block(KeyBlock(key=token))
    assert (await litellm.key_list("u1")).keys[0].blocked is True
    await t.http.post("/_test/fail", json={"endpoint": "/key/unblock", "status": 400, "times": 1})
    with pytest.raises(LiteLLMPermanentError):
        await litellm.key_unblock(KeyBlock(key=token))
    await litellm.key_unblock(KeyBlock(key=token))


async def test_invariants_flags_key_without_membership(fake_app, litellm, ro_engine):
    t = await _target(fake_app, litellm, ro_engine)
    await seed_team(t, members={"u1": {}})
    token = (await litellm.key_list("u1")).keys[0].token
    assert (await t.http.get("/_test/invariants")).json() == []
    await litellm.team_member_delete(TeamMemberDelete(team_id="team-c", user_id="u1"))
    assert (await t.http.get("/_test/invariants")).json() == [
        {"token": token, "user_id": "u1", "team_id": "team-c"}
    ]
