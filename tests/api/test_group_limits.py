from app.models import Grp
from app.outbox.worker import drain


async def test_team_tpm_and_metadata_model_tpm(login, put, provisioned, litellm, prov_settings):
    body = {"tpm_limit": 5000, "model_tpm_limits": {"gpt-4o": 2000}}
    r = await put(await login("rep"), "/groups/1/limits", body)
    assert r.status_code == 200, r.text
    assert r.json()["entities"][0]["sync_status"] == "pending"
    await drain(provisioned, litellm, prov_settings)
    team = (await litellm.team_info("A1")).team_info
    assert team.tpm_limit == 5000
    assert team.metadata["model_tpm_limit"] == {"gpt-4o": 2000}
    async with provisioned() as s:
        g = await s.get(Grp, 1)
        assert (g.tpm_limit, g.model_tpm_limits) == (5000, {"gpt-4o": 2000})


async def test_limits_permissions(login, put, provisioned):
    body = {"tpm_limit": 1, "model_tpm_limits": {}}
    assert (await put(await login("dana"), "/groups/1/limits", body)).status_code == 403
    assert (await put(await login("viewer"), "/groups/5/limits", body)).status_code == 404
    bad = {"tpm_limit": -1, "model_tpm_limits": {}}
    assert (await put(await login("admin"), "/groups/1/limits", bad)).status_code == 422
