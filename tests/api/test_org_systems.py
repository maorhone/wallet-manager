"""Step 5.4: Org Systems live in a virtual LiteLLM org; service keys can move into them."""

import uuid
from decimal import Decimal

from sqlalchemy import select

from app.models import AuditLog, LitellmWrite, Organization, ServiceKey
from app.outbox.worker import drain
from fake_litellm import db as fake_db


async def post(client, path, body, reason="org systems"):
    headers = {
        "X-CSRF-Token": client.cookies["wm_csrf"],
        "X-Change-Reason": reason,
        "Idempotency-Key": uuid.uuid4().hex,
    }
    return await client.post(path, json=body, headers=headers)


async def envelope(provisioned, amount=100000):
    async with provisioned() as s:
        (await s.get(Organization, 1)).systems_envelope = Decimal(amount)
        await s.commit()


def system(name="Sys", budget=40000, **extra):
    return {"name": name, "budget": budget, **extra}


async def test_virtual_org_created_once(login_wm, provisioned, litellm, prov_settings):
    await envelope(provisioned)
    admin = await login_wm("admin")
    first = await post(
        admin, "/organizations/1/systems", system("A", owner_upn="dana@corp.example")
    )
    assert first.status_code == 200, first.text
    second = await post(admin, "/organizations/1/systems", system("B"))
    assert second.status_code == 200, second.text
    await drain(provisioned, litellm, prov_settings)
    async with provisioned() as s:
        assert (await s.get(Organization, 1)).systems_litellm_org_id == "orgsys-1"
        orgs = (
            await s.scalars(select(LitellmWrite).where(LitellmWrite.entity == "systems_org"))
        ).all()
        assert [w.endpoint for w in orgs] == ["/organization/new"]
        assert all(w.status == "applied" for w in orgs)
    info = await litellm.organization_info("orgsys-1")
    assert info.litellm_budget_table.max_budget == 100000
    sys_id = first.json()["entities"][0]["id"]
    team = (await litellm.team_info(f"sys-{sys_id}")).team_info
    assert (team.organization_id, team.max_budget) == ("orgsys-1", 40000)


async def test_org_system_budget_within_envelope(login_wm, provisioned):
    await envelope(provisioned, 50000)
    admin = await login_wm("admin")
    assert (await post(admin, "/organizations/1/systems", system(budget=30000))).status_code == 200
    r = await post(admin, "/organizations/1/systems", system("B", 30000))
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "PARENT_OVERALLOCATED"
    assert (body["requested"], body["available"]) == (60000, 50000)


async def test_move_key_updates_team_via_outbox(
    login_wm, provisioned, litellm, prov_settings, fake_engine
):
    await envelope(provisioned)
    admin = await login_wm("admin")
    created = await post(admin, "/organizations/1/systems", system(budget=40000))
    target = int(created.json()["entities"][0]["id"])
    key = await post(
        admin,
        "/service-keys",
        {
            "owner_kind": "group", "owner_id": 1, "alias": "bot", "max_budget": 10,
            "max_parallel": 2, "rpm_limit": 100, "models": ["test-model"],
        },
    )  # fmt: skip
    key_id = int(key.json()["entities"][0]["id"])
    await drain(provisioned, litellm, prov_settings)

    r = await post(admin, f"/service-keys/{key_id}/move", {"target_system_id": target})
    assert r.status_code == 200, r.text
    assert r.json()["entities"][0]["sync_status"] == "pending"
    await drain(provisioned, litellm, prov_settings)
    async with provisioned() as s:
        row = await s.get(ServiceKey, key_id)
        assert (row.owner_kind, row.owner_id, row.status) == ("system", target, "active")
        audits = (
            await s.scalars(
                select(AuditLog).where(
                    AuditLog.entity == "service_key", AuditLog.request_id == r.json()["request_id"]
                )
            )
        ).all()
        assert len(audits) == 1
        token_hash = row.litellm_token_hash
    async with fake_engine.connect() as conn:
        team = await conn.scalar(
            select(fake_db.token.c.team_id).where(fake_db.token.c.token == token_hash)
        )
    assert team == f"sys-{target}"
    listed = await admin.get(f"/service-keys?owner_kind=system&owner_id={target}")
    assert [k["id"] for k in listed.json()["items"]] == [key_id]

    too_big = await post(
        admin, "/organizations/1/systems", system("Tiny", 5)
    )  # a second system with less budget than the key
    tiny = int(too_big.json()["entities"][0]["id"])
    r = await post(admin, f"/service-keys/{key_id}/move", {"target_system_id": tiny})
    assert r.status_code == 422, r.text


async def test_org_system_spend_not_in_department_totals(login_wm, provisioned, snapshot):
    await envelope(provisioned)
    admin = await login_wm("admin")
    created = await post(admin, "/organizations/1/systems", system(responsible_department_id=1))
    sys_id = created.json()["entities"][0]["id"]
    before = (await admin.get("/departments/1/summary")).json()["wallet"]["used"]
    await snapshot([("system", sys_id, None, 777)])
    after = (await admin.get("/departments/1/summary")).json()["wallet"]["used"]
    assert before == after


async def test_owner_view_only_403_on_writes(login_wm, provisioned):
    await envelope(provisioned)
    admin = await login_wm("admin")
    created = await post(admin, "/organizations/1/systems", system(owner_upn="dana@corp.example"))
    sys_id = int(created.json()["entities"][0]["id"])
    owner = await login_wm("dana")
    listed = await owner.get(f"/service-keys?owner_kind=system&owner_id={sys_id}")
    assert listed.status_code == 200, listed.text
    assert (await post(owner, "/organizations/1/systems", system("Mine"))).status_code == 403
    key = await post(
        owner,
        "/service-keys",
        {
            "owner_kind": "system", "owner_id": sys_id, "alias": "x", "max_budget": 1,
            "max_parallel": 1, "rpm_limit": 10, "models": [],
        },
    )  # fmt: skip
    assert key.status_code == 403
