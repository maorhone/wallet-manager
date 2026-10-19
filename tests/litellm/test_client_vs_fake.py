from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from app.litellm_client import (
    KeyBlock,
    KeyGenerate,
    KeyUpdate,
    LiteLLMClient,
    LiteLLMPermanentError,
    LiteLLMTransientError,
    ModelBudget,
    OrganizationMemberAdd,
    OrganizationNew,
    OrganizationUpdate,
    TeamMemberAdd,
    TeamMemberDelete,
    TeamMemberUpdate,
    TeamNew,
    TeamUpdate,
    UserNew,
)
from app.litellm_client.models import JwtKeyMappingNew, Member


async def _seed_team(litellm: LiteLLMClient) -> None:
    await litellm.organization_new(
        OrganizationNew(
            organization_id="org-a", organization_alias="A", max_budget=1000, budget_duration="1mo"
        )
    )
    await litellm.team_new(
        TeamNew(team_id="team-a", team_alias="Team A", organization_id="org-a", max_budget=500)
    )


async def test_health(litellm):
    assert await litellm.health() is True


async def test_organization_roundtrip(litellm):
    await litellm.organization_new(
        OrganizationNew(
            organization_id="org-a",
            organization_alias="A",
            max_budget=1000,
            budget_duration="1mo",
            models=["m1"],
        )
    )
    info = await litellm.organization_info("org-a")
    assert info.organization_alias == "A"
    assert info.models == ["m1"]
    assert info.litellm_budget_table.max_budget == 1000
    await litellm.organization_update(OrganizationUpdate(organization_id="org-a", max_budget=1200))
    info = await litellm.organization_info("org-a")
    assert info.litellm_budget_table.max_budget == 1200
    assert info.litellm_budget_table.budget_duration == "1mo"


async def test_organization_member_add_roundtrip(litellm):
    await _seed_team(litellm)
    await litellm.organization_member_add(
        OrganizationMemberAdd(
            organization_id="org-a", member=Member(user_id="rep@x", role="org_admin")
        )
    )
    info = await litellm.organization_info("org-a")
    assert [(m.user_id, m.user_role) for m in info.members] == [("rep@x", "org_admin")]


async def test_team_roundtrip(litellm):
    await _seed_team(litellm)
    await litellm.team_update(
        TeamUpdate(
            team_id="team-a",
            max_budget=400,
            model_max_budget={"m1": ModelBudget(budget_limit=100, time_period="1mo")},
            metadata={"model_tpm_limit": {"m1": 5000}},
            team_member_budget=10,
        )
    )
    info = await litellm.team_info("team-a")
    team = info.team_info
    assert team.organization_id == "org-a"
    assert team.max_budget == 400
    assert team.model_max_budget == {"m1": {"budget_limit": 100, "time_period": "1mo"}}
    assert team.metadata["model_tpm_limit"] == {"m1": 5000}
    assert team.metadata["team_member_budget"] == 10


async def test_team_member_roundtrip(litellm):
    await _seed_team(litellm)
    await litellm.team_member_add(
        TeamMemberAdd(
            team_id="team-a", member=Member(user_id="u@x", role="user"), max_budget_in_team=50
        )
    )
    members = (await litellm.team_info("team-a")).team_memberships
    assert members[0].litellm_budget_table.max_budget == 50
    expiry = datetime.now(UTC) + timedelta(days=3)
    await litellm.team_member_update(
        TeamMemberUpdate(
            team_id="team-a",
            user_id="u@x",
            role="admin",
            max_budget_in_team=60,
            budget_duration="1mo",
            temp_budget_increase=15,
            temp_budget_expiry=expiry,
            model_max_budget={"m1": ModelBudget(budget_limit=20, time_period="1mo")},
            tpm_limit=1000,
            rpm_limit=10,
        )
    )
    info = await litellm.team_info("team-a")
    budget = info.team_memberships[0].litellm_budget_table
    assert (budget.max_budget, budget.temp_budget_increase) == (60, 15)
    assert info.team_info.members_with_roles == [{"user_id": "u@x", "role": "admin"}]
    await litellm.team_member_delete(TeamMemberDelete(team_id="team-a", user_id="u@x"))
    info = await litellm.team_info("team-a")
    assert info.team_memberships == [] and info.team_info.members_with_roles == []


def test_team_member_update_temp_fields_go_together():
    with pytest.raises(ValidationError):
        TeamMemberUpdate(
            team_id="t", user_id="u", max_budget_in_team=1, budget_duration="1mo",
            temp_budget_increase=5,
        )  # fmt: skip


async def test_user_new_roundtrip(litellm):
    user = await litellm.user_new(UserNew(user_id="u@x", sso_user_id="u@x", user_email="u@x"))
    assert user.user_id == "u@x"
    with pytest.raises(LiteLLMPermanentError):
        await litellm.user_new(UserNew(user_id="u@x", sso_user_id="u@x", user_email="u@x"))


async def test_key_roundtrip(litellm):
    await _seed_team(litellm)
    gen = await litellm.key_generate(
        KeyGenerate(
            user_id="u@x",
            team_id="team-a",
            key_alias="u@x",
            max_parallel_requests=2,
            rpm_limit=100,
            model_tpm_limit={"m1": 1000},
        )
    )
    keys = (await litellm.key_list("u@x")).keys
    assert [k.token for k in keys] == [gen.token]
    assert keys[0].max_parallel_requests == 2
    assert keys[0].metadata["model_tpm_limit"] == {"m1": 1000}
    await litellm.key_update(KeyUpdate(key=gen.token, max_parallel_requests=4))
    assert (await litellm.key_list("u@x")).keys[0].max_parallel_requests == 4
    await litellm.key_block(KeyBlock(key=gen.token))
    assert (await litellm.key_list("u@x")).keys[0].blocked is True
    await litellm.key_unblock(KeyBlock(key=gen.token))
    assert (await litellm.key_list("u@x")).keys[0].blocked is False


async def test_key_generate_returns_hash_and_plaintext_once(litellm):
    import hashlib

    gen = await litellm.key_generate(KeyGenerate(user_id="u@x", key_alias="u@x"))
    assert gen.key.startswith("sk-")
    assert gen.token == hashlib.sha256(gen.key.encode()).hexdigest()
    listed = (await litellm.key_list("u@x")).keys
    assert gen.key not in listed[0].model_dump_json()
    # the plaintext key also works to address the key
    await litellm.key_block(KeyBlock(key=gen.key))
    assert (await litellm.key_list("u@x")).keys[0].blocked is True


def test_key_models_have_no_model_max_budget():
    for model in (KeyGenerate, KeyUpdate):
        assert "model_max_budget" not in model.model_fields
    with pytest.raises(ValidationError):
        KeyGenerate(user_id="u", model_max_budget={"m": {"budget_limit": 1, "time_period": "1d"}})


async def test_jwt_mapping_roundtrip(litellm):
    gen = await litellm.key_generate(KeyGenerate(user_id="u@x", key_alias="u@x"))
    mapping = await litellm.jwt_mapping_new(
        JwtKeyMappingNew(
            jwt_issuer="adfs", jwt_claim_name="upn", jwt_claim_value="u@x", token=gen.token
        )
    )
    assert (mapping.jwt_claim_value, mapping.token) == ("u@x", gen.token)


async def test_outage_raises_transient(litellm, fake_app):
    fake_app.state.fake.outage = True
    with pytest.raises(LiteLLMTransientError):
        await litellm.organization_info("org-a")
    with pytest.raises(LiteLLMTransientError):
        await litellm.health()
    fake_app.state.fake.outage = False
    assert await litellm.health() is True


async def test_outage_control_endpoints(fake_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as http:
        assert (await http.post("/_test/outage", json={"on": True})).status_code == 200
        assert (await http.get("/health/liveliness")).status_code == 503
        await http.post("/_test/outage", json={"on": False})
        assert (await http.get("/health/liveliness")).status_code == 200


async def test_connect_error_raises_transient():
    def boom(request):
        raise httpx.ConnectError("down")

    client = LiteLLMClient("http://x", "k", transport=httpx.MockTransport(boom))
    with pytest.raises(LiteLLMTransientError):
        await client.health()


async def test_4xx_raises_permanent(litellm):
    await _seed_team(litellm)
    with pytest.raises(LiteLLMPermanentError) as exc:
        await litellm.team_new(TeamNew(team_id="team-a", organization_id="org-a"))
    assert exc.value.status == 400
    assert "already exists" in str(exc.value.body)
    with pytest.raises(LiteLLMPermanentError) as exc:
        await litellm.team_info("nope")
    assert exc.value.status == 404


async def test_bad_master_key_is_permanent(fake_app):
    client = LiteLLMClient("http://fake", "wrong-key", transport=httpx.ASGITransport(app=fake_app))
    with pytest.raises(LiteLLMPermanentError) as exc:
        await client.organization_info("org-a")
    assert exc.value.status == 401
    assert "wrong-key" not in repr(exc.value)
