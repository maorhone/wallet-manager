"""§4, DR4, FR-K1, FR-K2: production organizations and systems."""

from sqlalchemy import func, select

from app.models import AuditLog, LitellmWrite
from app.outbox.worker import drain

D1, D2 = 1, 2


async def post(client, path, body, reason="production", **headers):
    h = {"X-CSRF-Token": client.cookies["wm_csrf"], **headers}
    if reason is not None:
        h["X-Change-Reason"] = reason
    return await client.post(path, json=body, headers=h)


async def test_create_production_org_then_update_budget(
    login_wm, provisioned, litellm, prov_settings, put
):
    admin = await login_wm("admin")
    r = await put(admin, f"/production/departments/{D1}", {"budget": 100000}, "set up")
    assert r.status_code == 200, r.text
    ent = r.json()["entities"][0]
    assert (ent["entity"], ent["id"], ent["sync_status"]) == ("production_org", "1", "pending")
    assert ent["values"]["litellm_org_id"] == "D1-prod"

    await drain(provisioned, litellm, prov_settings)
    info = await litellm.organization_info("D1-prod")
    assert info.organization_alias == "Department D1 production"
    assert info.litellm_budget_table.max_budget == 100000
    assert info.litellm_budget_table.budget_duration == "1mo"

    r = await put(admin, f"/production/departments/{D1}", {"budget": 150000}, "raise")
    assert r.status_code == 200, r.text
    await drain(provisioned, litellm, prov_settings)
    info = await litellm.organization_info("D1-prod")
    assert info.litellm_budget_table.max_budget == 150000

    get = await admin.get(f"/production/departments/{D1}")
    assert get.status_code == 200, get.text
    org = get.json()["org"]
    assert (org["litellm_org_id"], org["budget"], org["sync_status"]) == (
        "D1-prod", 150000, "applied",
    )  # fmt: skip


async def test_system_team_in_production_org_not_department_org_dr4(
    login_wm, provisioned, litellm, prov_settings, put
):
    admin = await login_wm("admin")
    await put(admin, f"/production/departments/{D1}", {"budget": 100000})
    r = await post(
        admin, f"/production/departments/{D1}/systems", {"name": "Sys A", "budget": 40000}
    )
    assert r.status_code == 200, r.text
    ent = r.json()["entities"][0]
    assert ent["entity"] == "production_system"
    sys_id = int(ent["id"])
    assert ent["values"]["litellm_team_id"] == f"sys-{sys_id}"

    await drain(provisioned, litellm, prov_settings)
    team = (await litellm.team_info(f"sys-{sys_id}")).team_info
    assert (team.organization_id, team.max_budget) == ("D1-prod", 40000)
    assert team.organization_id != "D1"


async def test_systems_over_production_budget_422_with_numbers(login_wm, provisioned, put):
    admin = await login_wm("admin")
    await put(admin, f"/production/departments/{D1}", {"budget": 50000})
    r = await post(
        admin, f"/production/departments/{D1}/systems", {"name": "Sys A", "budget": 30000}
    )
    assert r.status_code == 200, r.text

    r = await post(
        admin, f"/production/departments/{D1}/systems", {"name": "Sys B", "budget": 30000}
    )
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "PARENT_OVERALLOCATED"
    assert (body["requested"], body["available"], body["parent_total"]) == (60000, 50000, 50000)


async def test_system_without_production_org_invalid(login_wm, provisioned):
    admin = await login_wm("admin")
    r = await post(
        admin, f"/production/departments/{D2}/systems", {"name": "Sys A", "budget": 1000}
    )
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "INVALID_REQUEST"
    assert body["details"]["reason"] == "no_production_org"


async def test_rep_reads_viewer_and_head_403(login_wm, provisioned, put):
    admin = await login_wm("admin")
    await put(admin, f"/production/departments/{D1}", {"budget": 10000})

    rep = await login_wm("rep")
    assert (await rep.get(f"/production/departments/{D1}")).status_code == 200
    for who in ("viewer", "dana"):  # global viewer; group A1 head
        client = await login_wm(who)
        assert (await client.get(f"/production/departments/{D1}")).status_code == 403


async def test_non_admin_writes_403(login_wm, provisioned, put):
    rep = await login_wm("rep")
    r = await put(rep, f"/production/departments/{D1}", {"budget": 10000})
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"
    r = await post(rep, f"/production/departments/{D1}/systems", {"name": "X", "budget": 1})
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"


async def test_replay_same_idempotency_key_200(login_wm, seeded, put):
    admin = await login_wm("admin")
    first = await put(
        admin, f"/production/departments/{D1}", {"budget": 10000}, "k", **{"Idempotency-Key": "k1"}
    )
    second = await put(
        admin, f"/production/departments/{D1}", {"budget": 10000}, "k", **{"Idempotency-Key": "k1"}
    )
    assert first.status_code == 200 and second.status_code == 200
    rid = first.json()["request_id"]
    assert second.json()["request_id"] == rid
    async with seeded() as s:
        audits = (await s.scalar(select(func.count()).select_from(AuditLog))) or 0
        writes = (await s.scalar(select(func.count()).select_from(LitellmWrite))) or 0
    assert (audits, writes) == (1, 1)
