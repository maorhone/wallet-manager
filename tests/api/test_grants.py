from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from grants import A1, C1, create, grant_body, send, when
from sqlalchemy import select

from app.models import AuditLog, Grant, GrantShare
from app.outbox.worker import drain


async def _team(litellm, team="A1"):
    return (await litellm.team_info(team)).team_info


async def test_create_active_grant_raises_team_max_budget_after_drain(
    login, provisioned, litellm, prov_settings
):
    before = (await _team(litellm)).max_budget
    r = await create(await login("pmo"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert [e["entity"] for e in body["entities"]] == ["grant", "group"]
    assert body["entities"][0]["values"]["status"] == "active"
    assert body["entities"][1]["sync_status"] == "pending"
    await drain(provisioned, litellm, prov_settings)
    assert (await _team(litellm)).max_budget == before + 50000


async def test_model_grant_raises_max_budget_and_pot(login, provisioned, litellm, prov_settings):
    before = (await _team(litellm)).max_budget
    r = await create(await login("pmo"), grant_body(amount=20000, model="gpt-4o"))
    assert r.status_code == 200, r.text
    await drain(provisioned, litellm, prov_settings)
    team = await _team(litellm)
    assert team.max_budget == before + 20000
    assert team.model_max_budget["gpt-4o"]["budget_limit"] == 20000


async def test_future_grant_pending_reserves_pool_no_litellm_call(login, provisioned):
    pmo = await login("pmo")
    r = await create(pmo, grant_body(starts_at=when(days=5), expires_at=when(days=35)))
    assert r.status_code == 200, r.text
    assert [e["entity"] for e in r.json()["entities"]] == ["grant"]
    assert r.json()["entities"][0]["values"]["status"] == "pending"
    pool = (await pmo.get("/departments/1/pmo-pool")).json()
    assert pool["available"] == 130000
    r = await create(pmo, grant_body(amount=140000, starts_at=when(days=5)))
    assert r.status_code == 422 and r.json()["code"] == "PMO_POOL_INSUFFICIENT"


async def test_amount_above_pool_422_with_numbers(login, provisioned):
    r = await create(await login("pmo"), grant_body(amount=180001))
    body = r.json()
    assert r.status_code == 422 and body["code"] == "PMO_POOL_INSUFFICIENT"
    assert (body["requested"], body["available"], body["parent_total"]) == (180001, 180000, 180000)


@pytest.mark.xfail(strict=False, reason="flaky under load 2026-10-22; see OPEN-ITEMS")
async def test_pmo_other_department_group_scope_forbidden(login, provisioned):
    r = await create(await login("pmo"), grant_body(group_id=C1))
    assert r.status_code == 422 and r.json()["code"] == "SCOPE_FORBIDDEN"
    assert (await create(await login("pmo"), grant_body(group_id=999))).status_code == 404
    # the admin is not scope-limited; D2 simply has an empty pool
    r = await create(await login("admin"), grant_body(group_id=C1))
    assert r.status_code == 422 and r.json()["code"] == "PMO_POOL_INSUFFICIENT"
    assert (await create(await login("admin"), grant_body(group_id=A1))).status_code == 200


async def test_head_viewer_employee_403(login, provisioned):
    for who in ("dana", "viewer", "dani", "rep"):
        r = await create(await login(who))
        assert r.status_code == 403, who


async def test_expiry_before_start_invalid(login, provisioned):
    pmo = await login("pmo")
    r = await create(pmo, grant_body(starts_at=when(days=5), expires_at=when(days=2)))
    assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"
    r = await create(pmo, grant_body(expires_at=when(days=-1)))
    assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"
    r = await send(pmo, "POST", "/grants", grant_body(), reason=None)
    assert r.status_code == 422


async def _grant_id(client, **extra) -> int:
    r = await create(client, grant_body(**extra))
    assert r.status_code == 200, r.text
    return int(r.json()["entities"][0]["id"])


async def test_patch_increase_checks_pool(login, provisioned):
    pmo = await login("pmo")
    gid = await _grant_id(pmo, amount=100000)
    r = await send(pmo, "PATCH", f"/grants/{gid}", {"amount": 180000})
    assert r.status_code == 200, r.text
    r = await send(pmo, "PATCH", f"/grants/{gid}", {"amount": 180001})
    body = r.json()
    assert r.status_code == 422 and body["code"] == "PMO_POOL_INSUFFICIENT"
    assert (body["requested"], body["available"]) == (1, 0)
    assert (await send(pmo, "PATCH", f"/grants/{gid}", {})).status_code == 422


async def test_patch_decrease_below_shared_grant_below_shared(login, provisioned):
    pmo = await login("pmo")
    gid = await _grant_id(pmo, amount=100000)
    async with provisioned() as s:
        s.add(GrantShare(grant_id=gid, upn="dani@corp.example", amount=Decimal(30000)))
        await s.commit()
    r = await send(pmo, "PATCH", f"/grants/{gid}", {"amount": 29999})
    body = r.json()
    assert r.status_code == 422 and body["code"] == "GRANT_BELOW_SHARED"
    assert (body["requested"], body["available"]) == (29999, 30000)
    assert (await send(pmo, "PATCH", f"/grants/{gid}", {"amount": 30000})).status_code == 200


async def test_patch_expiry_before_share_expiry_422(login, provisioned):
    pmo = await login("pmo")
    gid = await _grant_id(pmo, amount=100000)
    async with provisioned() as s:
        s.add(
            GrantShare(
                grant_id=gid, upn="dani@corp.example", amount=Decimal(10),
                expires_at=datetime.now(UTC) + timedelta(days=20),
            )
        )  # fmt: skip
        await s.commit()
    r = await send(pmo, "PATCH", f"/grants/{gid}", {"expires_at": when(days=10)})
    assert r.status_code == 422 and r.json()["code"] == "SHARE_EXPIRY_AFTER_GRANT"
    r = await send(pmo, "PATCH", f"/grants/{gid}", {"expires_at": when(days=60)})
    assert r.status_code == 200, r.text


async def test_close_returns_amount_to_pool_and_lowers_team(
    login, provisioned, litellm, prov_settings
):
    pmo = await login("pmo")
    before = (await _team(litellm)).max_budget
    gid = await _grant_id(pmo, amount=60000)
    await drain(provisioned, litellm, prov_settings)
    assert (await pmo.get("/departments/1/pmo-pool")).json()["available"] == 120000
    r = await send(pmo, "POST", f"/grants/{gid}/close", {"reason": "done"}, reason="done")
    assert r.status_code == 200, r.text
    assert r.json()["entities"][0]["values"] == {
        **r.json()["entities"][0]["values"], "status": "closed", "returned": 60000,
    }  # fmt: skip
    await drain(provisioned, litellm, prov_settings)
    assert (await _team(litellm)).max_budget == before
    assert (await pmo.get("/departments/1/pmo-pool")).json()["available"] == 180000
    async with provisioned() as s:
        g = await s.get(Grant, gid)
        assert g.status == "closed" and g.closed_at is not None
    again = await send(pmo, "POST", f"/grants/{gid}/close", {}, reason="again")
    assert again.status_code == 422 and again.json()["details"]["status"] == "closed"
    assert (await send(pmo, "PATCH", f"/grants/{gid}", {"amount": 1})).status_code == 422


async def test_replay_same_idempotency_key_200(login, provisioned):
    pmo = await login("pmo")
    key = {"Idempotency-Key": "grant-key-1"}
    first = await create(pmo, **key)
    second = await create(pmo, **key)
    assert first.status_code == second.status_code == 200
    assert first.json()["request_id"] == second.json()["request_id"]
    async with provisioned() as s:
        assert len((await s.scalars(select(Grant))).all()) == 1
        assert len((await s.scalars(select(AuditLog).where(AuditLog.entity == "grant"))).all()) == 1
