"""Step 5.6: Internal Usage initiatives (D-067)."""

import uuid

from sqlalchemy import select

from app.models import Initiative

G1 = 1


async def send(client, method, path, body=None):
    headers = {
        "X-CSRF-Token": client.cookies["wm_csrf"],
        "X-Change-Reason": "initiative",
        "Idempotency-Key": uuid.uuid4().hex,
    }
    return await client.request(method, path, json=body, headers=headers)


def initiative(**changes):
    return {"name": "Pilot", "owner_upn": "dani@corp.example", "budget": 1000, **changes}


async def allocated(client):
    r = await client.get(f"/groups/{G1}/summary")
    assert r.status_code == 200, r.text
    return r.json()["wallet"]["unallocated"]


async def test_section_head_creates_initiative_budget_carved(login_wm, provisioned):
    head = await login_wm("dana")
    before = await allocated(head)
    r = await send(head, "POST", f"/groups/{G1}/initiatives", initiative())
    assert r.status_code == 200, r.text
    assert await allocated(head) == before - 1000
    async with provisioned() as s:
        row = (await s.scalars(select(Initiative))).one()
        assert (row.group_id, row.status, row.created_by) == (G1, "active", "dana@corp.example")
    over = await send(head, "POST", f"/groups/{G1}/initiatives", initiative(name="X", budget=10**8))
    assert over.status_code == 422 and over.json()["code"] == "PARENT_OVERALLOCATED", over.text


async def test_duplicate_name_409(login_wm, provisioned):
    head = await login_wm("dana")
    assert (await send(head, "POST", f"/groups/{G1}/initiatives", initiative())).status_code == 200
    r = await send(head, "POST", f"/groups/{G1}/initiatives", initiative())
    assert r.status_code == 409, r.text


async def test_key_attached_gets_max_budget(login_wm, provisioned):
    head = await login_wm("dana")
    r = await send(head, "POST", f"/groups/{G1}/initiatives", initiative())
    iid = int(r.json()["entities"][0]["id"])
    key = {
        "owner_kind": "initiative",
        "owner_id": iid,
        "alias": "bot",
        "max_budget": 400,
        "max_parallel": 2,
        "rpm_limit": 100,
        "models": ["test-model"],
    }
    ok = await send(head, "POST", "/service-keys", key)
    assert ok.status_code == 200, ok.text
    over = await send(head, "POST", "/service-keys", {**key, "max_budget": 700})
    assert over.status_code == 422 and over.json()["code"] == "PARENT_OVERALLOCATED", over.text
    got = (await head.get(f"/initiatives/{iid}")).json()
    assert got["tech_status"] == "connected"
    keys = (await head.get(f"/service-keys?owner_kind=initiative&owner_id={iid}")).json()["items"]
    assert [k["max_budget"] for k in keys] == [400]


async def test_record_without_key_is_not_connected(login_wm, provisioned):
    head = await login_wm("dana")
    r = await send(head, "POST", f"/groups/{G1}/initiatives", initiative())
    iid = int(r.json()["entities"][0]["id"])
    got = (await head.get(f"/initiatives/{iid}")).json()
    assert got["tech_status"] == "not_connected"


async def test_owner_read_only(login_wm, provisioned):
    head = await login_wm("dana")
    r = await send(
        head, "POST", f"/groups/{G1}/initiatives", initiative(owner_upn="dani@corp.example")
    )
    iid = int(r.json()["entities"][0]["id"])
    owner = await login_wm("dani")
    assert (await owner.get(f"/initiatives/{iid}")).status_code == 200
    listed = await owner.get(f"/groups/{G1}/initiatives")
    assert [i["id"] for i in listed.json()["items"]] == [iid]
    denied = await send(owner, "POST", f"/groups/{G1}/initiatives", initiative(name="Mine"))
    assert denied.status_code == 403, denied.text
    key = {
        "owner_kind": "initiative",
        "owner_id": iid,
        "alias": "bot",
        "max_budget": 10,
        "max_parallel": 2,
        "rpm_limit": 100,
        "models": [],
    }
    assert (await send(owner, "POST", "/service-keys", key)).status_code == 403


async def _initiative_key(head, *, budget=10, limit=10):
    created = await send(head, "POST", f"/groups/{G1}/initiatives", initiative(budget=budget))
    assert created.status_code == 200, created.text
    iid = int(created.json()["entities"][0]["id"])
    body = {
        "owner_kind": "initiative",
        "owner_id": iid,
        "alias": "bot",
        "max_budget": limit,
        "max_parallel": 2,
        "rpm_limit": 100,
        "models": ["test-model"],
    }
    first = await send(head, "POST", "/service-keys", body)
    assert first.status_code == 200, first.text
    return body, first.json()


async def test_spent_revoked_initiative_key_cannot_replenish_grant(
    login_wm, provisioned, fake_app, litellm, prov_settings
):
    from app.outbox.worker import drain
    from tests.api.test_service_keys import chat

    head = await login_wm("dana")
    body, first = await _initiative_key(head)
    assert (await chat(fake_app, first["secret"])).status_code == 200
    kid = first["entities"][0]["id"]
    assert (await send(head, "POST", f"/service-keys/{kid}/revoke", {})).status_code == 200
    await drain(provisioned, litellm, prov_settings)
    rejected = await send(head, "POST", "/service-keys", body)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "PARENT_OVERALLOCATED"


async def test_spent_initiative_rotation_cannot_replenish_grant(login_wm, provisioned, fake_app):
    from tests.api.test_service_keys import chat

    head = await login_wm("dana")
    _, first = await _initiative_key(head)
    assert (await chat(fake_app, first["secret"])).status_code == 200
    kid = first["entities"][0]["id"]
    rejected = await send(head, "POST", f"/service-keys/{kid}/rotate", {})
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "PARENT_OVERALLOCATED"
    # A rejected rotation leaves the existing credential usable.
    assert (await chat(fake_app, first["secret"])).status_code == 200


async def test_rotation_reserves_both_generations_within_initiative_grant(
    login_wm, provisioned, fake_app, litellm, prov_settings
):
    from decimal import Decimal

    from app.models import ServiceKey
    from app.outbox.worker import drain
    from tests.api.test_service_keys import chat

    head = await login_wm("dana")
    body, first = await _initiative_key(head, budget=20)
    assert (await chat(fake_app, first["secret"])).status_code == 200
    kid = first["entities"][0]["id"]
    rotated = await send(head, "POST", f"/service-keys/{kid}/rotate", {})
    assert rotated.status_code == 200, rotated.text
    assert (await chat(fake_app, rotated.json()["secret"])).status_code == 200
    await drain(provisioned, litellm, prov_settings)
    async with provisioned() as db:
        row = await db.get(ServiceKey, int(kid))
        assert row.retired_budget == Decimal("10")
    again = await send(head, "POST", f"/service-keys/{kid}/rotate", {})
    assert again.status_code == 422, again.text
    assert (await send(head, "POST", f"/service-keys/{kid}/revoke", {})).status_code == 200
    await drain(provisioned, litellm, prov_settings)
    assert (await send(head, "POST", "/service-keys", body)).status_code == 422


async def test_failed_rotation_keeps_ambiguous_generation_reserved(login_wm, provisioned, fake_app):
    from decimal import Decimal

    from app.models import ServiceKey

    head = await login_wm("dana")
    body, first = await _initiative_key(head, budget=20)
    kid = first["entities"][0]["id"]
    fake_app.state.fake.outage = True
    failed = await send(head, "POST", f"/service-keys/{kid}/rotate", {})
    assert failed.status_code == 503, failed.text
    fake_app.state.fake.outage = False
    async with provisioned() as db:
        key = await db.get(ServiceKey, int(kid))
        assert key.status == "active"
        assert key.retired_budget == Decimal("10")
    # A timeout may hide upstream success; a fresh attempt must not replenish credit.
    blocked = await send(head, "POST", "/service-keys", body)
    assert blocked.status_code == 422, blocked.text
