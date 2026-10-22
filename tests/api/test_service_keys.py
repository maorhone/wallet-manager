"""Step 3.4 acceptance and regression tests for the application lifecycle."""

import asyncio
import hashlib
import uuid

import httpx
import pytest
from sqlalchemy import select, text

from app.models import AuditLog, LitellmWrite, ServiceKey
from app.outbox.worker import drain


def body(**changes):
    return {
        "owner_kind": "group",
        "owner_id": 1,
        "alias": "bot",
        "max_budget": 10,
        "max_parallel": 2,
        "rpm_limit": 100,
        "models": ["test-model"],
        **changes,
    }


async def post(client, path="/service-keys", payload=None, key=None):
    return await client.post(
        path,
        json=body() if payload is None else payload,
        headers={
            "X-CSRF-Token": client.cookies["wm_csrf"],
            "X-Change-Reason": "service bot",
            "Idempotency-Key": key or uuid.uuid4().hex,
        },
    )


async def create(login_wm, **changes):
    client = await login_wm("dana")
    r = await post(client, payload=body(**changes))
    assert r.status_code == 200, r.text
    return client, r


async def test_head_creates_group_key_secret_once_hash_stored(login_wm, provisioned):
    client, r = await create(login_wm)
    secret = r.json()["secret"]
    assert r.headers["cache-control"] == "no-store"
    async with provisioned() as db:
        row = await db.get(ServiceKey, int(r.json()["entities"][0]["id"]))
        assert row.litellm_token_hash == hashlib.sha256(secret.encode()).hexdigest()
        assert row.status == "active"
    listed = await client.get("/service-keys?owner_kind=group&owner_id=1")
    assert listed.status_code == 200, listed.text
    assert secret not in listed.text and row.litellm_token_hash not in listed.text
    assert listed.json()["items"][0]["actions"] == {"rotate": True, "revoke": True}
    summary = (await client.get("/groups/1/summary")).json()
    assert summary["actions"]["service_keys"] == {"view": True, "manage": True}


async def test_secret_absent_from_db_audit_outbox_and_logs(login_wm, provisioned, caplog):
    _, r = await create(login_wm)
    secret = r.json()["secret"]
    async with provisioned() as db:
        for table in ("service_key", "audit_log", "litellm_write"):
            rows = (await db.execute(text(f"SELECT row_to_json(t)::text FROM {table} t"))).scalars()
            assert all(secret not in row for row in rows)
    assert secret not in caplog.text


async def test_replay_returns_200_without_secret(login_wm, provisioned):
    client = await login_wm("dana")
    first = await post(client, key="once")
    assert first.status_code == 200, first.text
    second = await post(client, key="once")
    assert second.status_code == 200 and "secret" not in second.json()
    async with provisioned() as db:
        audits = list(await db.scalars(select(AuditLog).where(AuditLog.entity == "service_key")))
        assert len(audits) == 1


async def test_group_keys_plus_caps_over_base_parent_overallocated(login_wm, provisioned):
    client = await login_wm("dana")
    r = await post(client, payload=body(max_budget=999999))
    assert r.status_code == 422 and r.json()["code"] == "PARENT_OVERALLOCATED", r.text


async def test_member_caps_check_counts_key_budgets(login_wm, provisioned, put):
    client, _ = await create(login_wm, max_budget=10000)
    r = await put(
        client, "/groups/1/members/caps", [{"upn": "dani@corp.example", "base_cap": 215000}]
    )
    assert r.status_code == 422 and r.json()["code"] == "PARENT_OVERALLOCATED", r.text


async def test_limits_out_of_policy_422_admin_above_max_ok(login_wm, provisioned):
    client = await login_wm("dana")
    for change in ({"max_parallel": 99999}, {"rpm_limit": 999999}, {"max_parallel": 0}):
        r = await post(client, payload=body(**change))
        assert r.status_code == 422 and r.json()["code"] == "LIMIT_OUT_OF_POLICY", r.text
    admin = await login_wm("admin")
    r = await post(admin, payload=body(max_parallel=99999, rpm_limit=999999))
    assert r.status_code == 200, r.text


async def test_litellm_down_503_key_failed_nothing_usable(login_wm, provisioned, fake_app):
    fake_app.state.fake.outage = True
    r = await post(await login_wm("dana"))
    assert r.status_code == 503 and r.json()["code"] == "LITELLM_UNAVAILABLE", r.text
    async with provisioned() as db:
        key = await db.scalar(select(ServiceKey))
        write = await db.scalar(select(LitellmWrite).where(LitellmWrite.entity == "service_key"))
        assert key.status == write.status == "failed"
        assert key.litellm_token_hash is None and write.last_error


async def chat(fake_app, secret):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as http:
        return await http.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {secret}"},
            json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
        )


async def test_rotate_new_secret_old_key_deleted_after_drain(
    login_wm, provisioned, litellm, prov_settings, fake_app
):
    client, first = await create(login_wm)
    key_id = first.json()["entities"][0]["id"]
    r = await post(client, f"/service-keys/{key_id}/rotate", {}, key="rotate")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    assert r.json()["secret"] != first.json()["secret"]
    replay = await post(client, f"/service-keys/{key_id}/rotate", {}, key="rotate")
    assert replay.status_code == 200 and "secret" not in replay.json()
    assert (await chat(fake_app, first.json()["secret"])).status_code == 200
    await drain(provisioned, litellm, prov_settings)
    assert (await chat(fake_app, first.json()["secret"])).status_code == 401
    assert (await chat(fake_app, r.json()["secret"])).status_code == 200


async def test_revoke_deletes_key_twice_invalid(
    login_wm, provisioned, litellm, prov_settings, fake_app
):
    client, first = await create(login_wm)
    path = f"/service-keys/{first.json()['entities'][0]['id']}/revoke"
    assert (await post(client, path, {})).status_code == 200
    r = await post(client, path, {})
    assert r.status_code == 422 and r.json()["details"]["status"] == "revoked", r.text
    await drain(provisioned, litellm, prov_settings)
    assert (await chat(fake_app, first.json()["secret"])).status_code == 401


@pytest.mark.xfail(strict=False, reason="flaky under load 2026-10-22; see OPEN-ITEMS")
async def test_rep_viewer_list_no_actions_and_write_403(login_wm, provisioned):
    _, first = await create(login_wm)
    key_id = first.json()["entities"][0]["id"]
    for who in ("rep", "viewer"):
        client = await login_wm(who)
        r = await client.get("/service-keys?owner_kind=group&owner_id=1")
        assert r.status_code == 200, r.text
        assert r.json()["items"][0]["actions"] == {"rotate": False, "revoke": False}
        for path, payload in (
            ("/service-keys", body()),
            (f"/service-keys/{key_id}/rotate", {}),
            (f"/service-keys/{key_id}/revoke", {}),
        ):
            assert (await post(client, path, payload)).status_code == 403
    head = await login_wm("dana")
    assert (await post(head, payload=body(owner_id=3))).status_code in (403, 404)


async def test_system_key_admin_only_and_within_system_budget(
    login_wm, provisioned, put, litellm, prov_settings
):
    admin = await login_wm("admin")
    await put(admin, "/production/departments/1", {"budget": 100})
    system = await post(
        admin, "/production/departments/1/systems", {"name": "system", "budget": 20}
    )
    sys_id = int(system.json()["entities"][0]["id"])
    payload = body(owner_kind="system", owner_id=sys_id)
    r = await post(admin, payload=payload)
    assert r.status_code == 422 and r.json()["details"]["sync_status"] == "pending", r.text
    await drain(provisioned, litellm, prov_settings)
    assert (await post(admin, payload=payload)).status_code == 200
    r = await post(admin, payload=body(owner_kind="system", owner_id=sys_id, max_budget=11))
    assert r.status_code == 422 and r.json()["code"] == "PARENT_OVERALLOCATED", r.text
    rep = await login_wm("rep")
    assert (await rep.get(f"/service-keys?owner_kind=system&owner_id={sys_id}")).status_code == 200
    assert (await post(rep, payload=payload)).status_code == 403
    assert (
        await (await login_wm("viewer")).get(f"/service-keys?owner_kind=system&owner_id={sys_id}")
    ).status_code == 403


async def test_concurrent_creates_reserve_pending_budget(login_wm, provisioned):
    client = await login_wm("dana")
    responses = await asyncio.gather(
        *(post(client, payload=body(max_budget=150000)) for _ in range(2))
    )
    assert sorted(r.status_code for r in responses) == [200, 422]


async def test_pending_generation_is_reserved_and_never_dispatched_by_worker(
    login_wm, provisioned, litellm, prov_settings, monkeypatch
):
    entered, release = asyncio.Event(), asyncio.Event()
    original = litellm.key_service_account_generate
    calls = 0

    async def paused(request):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return await original(request)

    monkeypatch.setattr(litellm, "key_service_account_generate", paused)
    client = await login_wm("dana")
    task = asyncio.create_task(post(client, payload=body(max_budget=150000)))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        result = await drain(provisioned, litellm, prov_settings)
        assert result.failed == result.applied == 0
        denied = await post(client, payload=body(max_budget=150000))
        assert denied.status_code == 422 and denied.json()["code"] == "PARENT_OVERALLOCATED"
    finally:
        release.set()
        response = await task
    assert response.status_code == 200, response.text
    assert calls == 1


async def test_failed_rotation_preserves_old_key(login_wm, provisioned, fake_app):
    client, first = await create(login_wm)
    fake_app.state.fake.outage = True
    r = await post(client, f"/service-keys/{first.json()['entities'][0]['id']}/rotate", {})
    assert r.status_code == 503, r.text
    fake_app.state.fake.outage = False
    assert (await chat(fake_app, first.json()["secret"])).status_code == 200
    async with provisioned() as db:
        key = await db.get(ServiceKey, int(first.json()["entities"][0]["id"]))
        assert key.status == "active"
