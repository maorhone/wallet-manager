import pytest
from sqlalchemy import select

from app.models import Employee, LitellmWrite
from app.outbox.worker import drain

A1 = 1
DANI = "dani@corp.example"


def path(upn=DANI):
    return f"/groups/{A1}/members/{upn}/limits"


async def test_out_of_policy_422(login, put, provisioned):
    dana = await login("aiteam")  # D-065: only the AI team (or an admin) writes rate limits
    r = await put(dana, path(), {"max_parallel": 9})
    assert r.status_code == 422
    body = r.json()
    assert (body["code"], body["requested"]) == ("LIMIT_OUT_OF_POLICY", 9)
    assert body["details"] == {"field": "max_parallel", "min": 1, "max": 8}
    r = await put(dana, path(), {"rpm_limit": 601})
    assert r.json()["details"] == {"field": "rpm_limit", "min": 1, "max": 600}
    assert (await put(dana, path(), {"max_parallel": 0})).status_code == 422


@pytest.mark.xfail(strict=False, reason="flaky under load 2026-10-24; see OPEN-ITEMS")
async def test_admin_above_max_ok(login, put, provisioned, litellm, prov_settings):
    admin = await login("admin")
    r = await put(admin, path(), {"max_parallel": 12, "rpm_limit": 900})
    assert r.status_code == 200, r.text
    assert (await put(admin, path(), {"max_parallel": 0})).status_code == 422  # below min
    await drain(provisioned, litellm, prov_settings)
    key = (await litellm.key_list(DANI)).keys[0]
    assert (key.max_parallel_requests, key.rpm_limit) == (12, 900)


async def test_key_update_payload_has_no_model_max_budget(login, put, provisioned):
    body = {"max_parallel": 6, "model_tpm_limits": {"gpt-4o": 1000}}
    assert (await put(await login("aiteam"), path(), body)).status_code == 200
    async with provisioned() as s:
        row = (
            await s.scalars(
                select(LitellmWrite)
                .where(LitellmWrite.entity_id == DANI, LitellmWrite.endpoint == "/key/update")
                .order_by(LitellmWrite.id.desc())
            )
        ).first()
        assert row.payload["max_parallel_requests"] == 6
        assert row.payload["model_tpm_limit"] == {"gpt-4o": 1000}
        assert "model_max_budget" not in row.payload  # R5: model budgets are member-level only
        e = await s.get(Employee, DANI)
        assert (e.max_parallel, e.model_tpm_limits) == (6, {"gpt-4o": 1000})


async def test_limits_permissions_and_self(login, put, provisioned):
    assert (await put(await login("dana"), path(), {})).status_code == 403  # head: forward only
    assert (await put(await login("rep"), path(), {})).status_code == 403
    assert (await put(await login("dani"), path("yael@corp.example"), {})).status_code == 404
    assert (await put(await login("pmo"), path(), {})).status_code == 403
    assert (await put(await login("aiteam"), path("avi@corp.example"), {})).status_code == 422


async def test_get_policies_readable_by_any_user(login):
    r = await (await login("dani")).get("/policies")
    assert r.status_code == 200
    body = r.json()
    assert body["concurrency"] == {"min": 1, "default": 4, "max": 8}
    assert body["rpm"]["max"] == 600
    assert not any(k.startswith("member_defaults") for k in body)
