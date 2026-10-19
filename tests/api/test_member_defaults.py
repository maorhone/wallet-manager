from app.models import Grp, Policy
from app.outbox.worker import drain

A1 = 1


async def test_team_member_budget_fields_written(login, put, provisioned, litellm, prov_settings):
    body = {"cap": 12000, "model_caps": {"gpt-4o": 500, "haiku": 0}}
    r = await put(await login("dana"), f"/groups/{A1}/member-defaults", body)
    assert r.status_code == 200, r.text
    assert r.json()["entities"][0]["sync_status"] == "pending"
    await drain(provisioned, litellm, prov_settings)
    meta = (await litellm.team_info("A1")).team_info.metadata
    assert meta["team_member_budget"] == 12000
    assert meta["team_member_budget_duration"] == "1mo"
    assert meta["team_member_model_max_budget"]["gpt-4o"]["budget_limit"] == 500
    assert "haiku" not in meta["team_member_model_max_budget"]
    async with provisioned() as s:
        assert (await s.get(Grp, A1)).default_member_cap == 12000
        row = await s.get(Policy, f"member_defaults.group.{A1}")
        assert row.value == {"model_caps": {"gpt-4o": 500.0}}
    again = {"cap": 1, "model_caps": {}}
    assert (
        await put(await login("admin"), f"/groups/{A1}/member-defaults", again)
    ).status_code == 200


async def test_member_defaults_permissions(login, put, provisioned):
    body = {"cap": 1, "model_caps": {}}
    assert (await put(await login("rep"), f"/groups/{A1}/member-defaults", body)).status_code == 403
    assert (await put(await login("noa"), f"/groups/{A1}/member-defaults", body)).status_code == 403
    assert (await put(await login("viewer"), "/groups/5/member-defaults", body)).status_code == 404
