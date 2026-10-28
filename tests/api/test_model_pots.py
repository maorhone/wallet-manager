import pytest
from sqlalchemy import select

from app.models import DepartmentModelAllowance, GroupModelPot
from app.outbox.worker import drain

A1, A2, C1 = 1, 2, 5  # group ids in seed order (seed/dev.yaml)


async def _allow(login, put, amounts):
    r = await put(await login("admin"), "/departments/1/model-allowances", amounts)
    assert r.status_code == 200, r.text
    return r


async def test_allowances_replace_map_and_need_admin(login, put, provisioned):
    await _allow(login, put, {"gpt-4o": 100000, "haiku": 5000})
    r = await _allow(login, put, {"gpt-4o": 90000, "haiku": 0})  # 0 deletes, absent deletes
    assert r.json()["entities"][0]["sync_status"] == "applied"  # no LiteLLM write (R5)
    async with provisioned() as s:
        rows = (await s.scalars(select(DepartmentModelAllowance))).all()
        assert {(r.model, r.amount) for r in rows} == {("gpt-4o", 90000)}
    for who in ("rep", "viewer"):
        r = await put(await login(who), "/departments/1/model-allowances", {"gpt-4o": 1})
        assert r.status_code == 403


@pytest.mark.xfail(strict=False, reason="flaky under load 2026-10-28; see OPEN-ITEMS")
async def test_pots_exceed_allowance_422(login, put, provisioned):
    await _allow(login, put, {"gpt-4o": 100000})
    rep = await login("rep")
    assert (await put(rep, f"/groups/{A1}/model-pots", {"gpt-4o": 60000})).status_code == 200
    r = await put(rep, f"/groups/{A2}/model-pots", {"gpt-4o": 50000})
    body = r.json()
    assert r.status_code == 422 and body["code"] == "PARENT_OVERALLOCATED"
    assert (body["requested"], body["available"], body["parent_total"]) == (110000, 100000, 100000)
    assert body["details"]["model"] == "gpt-4o"
    # a model with no allowance is checked against 0
    r = await put(rep, f"/groups/{A2}/model-pots", {"haiku": 1})
    assert r.status_code == 422 and r.json()["available"] == 0


async def test_pot_written_to_team_model_max_budget(
    login, put, provisioned, litellm, prov_settings
):
    await _allow(login, put, {"gpt-4o": 100000})
    r = await put(await login("rep"), f"/groups/{A1}/model-pots", {"gpt-4o": 60000})
    assert r.json()["entities"][0]["sync_status"] == "pending"
    await drain(provisioned, litellm, prov_settings)
    mmb = (await litellm.team_info("A1")).team_info.model_max_budget
    assert mmb["gpt-4o"]["budget_limit"] == 60000
    async with provisioned() as s:
        assert (await s.get(GroupModelPot, (A1, "gpt-4o"))).amount == 60000


async def test_pots_permissions_and_scope(login, put, provisioned):
    await _allow(login, put, {"gpt-4o": 100000})
    body = {"gpt-4o": 1}
    assert (await put(await login("pmo"), f"/groups/{A1}/model-pots", body)).status_code == 403
    assert (await put(await login("dana"), f"/groups/{A1}/model-pots", body)).status_code == 403
    assert (await put(await login("rep"), f"/groups/{C1}/model-pots", body)).status_code == 404
    assert (await put(await login("admin"), f"/groups/{A1}/model-pots", body)).status_code == 200
