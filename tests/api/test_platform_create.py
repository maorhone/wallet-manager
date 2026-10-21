"""§11.1 POST /departments, POST /departments/{id}/groups; §9.3 New budget group; D13."""

from app.models import Department, Grp
from app.outbox.worker import drain

D1 = 1


async def post(client, path, body, reason="create", **headers):
    h = {"X-CSRF-Token": client.cookies["wm_csrf"], **headers}
    if reason is not None:
        h["X-Change-Reason"] = reason
    return await client.post(path, json=body, headers=h)


async def test_create_department_enqueues_organization_new(
    login_wm, provisioned, litellm, prov_settings
):
    admin = await login_wm("admin")
    body = {
        "name": "Department D3", "litellm_org_id": "D3",
        "ad_group_dn": "CN=dept-d3,OU=Groups,DC=corp,DC=example", "budget": 400000,
    }  # fmt: skip
    r = await post(admin, "/departments", body)
    assert r.status_code == 200, r.text
    ent = r.json()["entities"][0]
    assert (ent["entity"], ent["sync_status"]) == ("department", "pending")
    dept_id = int(ent["id"])

    await drain(provisioned, litellm, prov_settings)
    info = await litellm.organization_info("D3")
    assert info.organization_alias == "Department D3"
    assert info.litellm_budget_table.max_budget == 400000
    assert info.litellm_budget_table.budget_duration == "1mo"
    async with provisioned() as s:
        d = await s.get(Department, dept_id)
        assert (d.name, d.litellm_org_id, d.budget_period) == ("Department D3", "D3", "1mo")


async def test_create_group_team_new_zero_budget_after_drain(
    login_wm, provisioned, litellm, prov_settings
):
    admin = await login_wm("admin")
    body = {
        "name": "Group A3",
        "litellm_team_id": "A3",
        "ad_group_dn": "CN=grp-a3,OU=Groups,DC=corp,DC=example",
    }
    r = await post(admin, f"/departments/{D1}/groups", body)
    assert r.status_code == 200, r.text
    ent = r.json()["entities"][0]
    assert (ent["entity"], ent["sync_status"]) == ("group", "pending")
    assert ent["values"]["default_member_cap"] == 0
    group_id = int(ent["id"])

    await drain(provisioned, litellm, prov_settings)
    team = (await litellm.team_info("A3")).team_info
    assert (team.max_budget, team.organization_id) == (0, "D1")
    async with provisioned() as s:
        g = await s.get(Grp, group_id)
        assert (g.base_budget, g.status, g.kind) == (0, "active", "regular")


async def test_second_staff_group_invalid(login_wm, provisioned):
    admin = await login_wm("admin")
    body = {
        "name": "Another staff", "litellm_team_id": "d1-staff-2",
        "ad_group_dn": "CN=grp-d1-staff-2,OU=Groups,DC=corp,DC=example", "kind": "department_staff",
    }  # fmt: skip
    r = await post(admin, f"/departments/{D1}/groups", body)
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "INVALID_REQUEST"
    assert body["details"]["field"] == "kind"


async def test_duplicate_team_id_invalid(login_wm, provisioned):
    admin = await login_wm("admin")
    body = {
        "name": "Dup",
        "litellm_team_id": "A1",
        "ad_group_dn": "CN=dup,OU=Groups,DC=corp,DC=example",
    }
    r = await post(admin, f"/departments/{D1}/groups", body)
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "INVALID_REQUEST"
    assert body["details"]["field"] == "litellm_team_id"


async def test_non_admin_403(login_wm, provisioned):
    rep = await login_wm("rep")
    dept_body = {"name": "X", "litellm_org_id": "org-x", "budget": 1000}
    assert (await post(rep, "/departments", dept_body)).status_code == 403
    group_body = {"name": "X", "litellm_team_id": "team-x"}
    assert (await post(rep, f"/departments/{D1}/groups", group_body)).status_code == 403
