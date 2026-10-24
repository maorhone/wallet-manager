"""Step 5.22: an organization's two separate envelopes (departments / Org Systems)."""

import uuid
from decimal import Decimal

from sqlalchemy import select

from app.directory import FixtureDirectory
from app.models import Department, Grp


def hdrs(client):
    return {
        "X-CSRF-Token": client.cookies["wm_csrf"],
        "X-Change-Reason": "envelopes",
        "Idempotency-Key": uuid.uuid4().hex,
    }


async def put(client, path, body):
    return await client.put(path, json=body, headers=hdrs(client))


async def allocator_client(login_wm, provisioned):
    async with provisioned() as s:
        dn = (await s.scalar(select(Grp).where(Grp.litellm_team_id == "B1"))).ad_group_dn
    admin = await login_wm("admin")
    bind = {
        "scope_kind": "organization", "scope_id": 1, "role": "org_allocator", "ad_group_dns": [dn],
    }  # fmt: skip
    assert (await put(admin, "/role-bindings", bind)).status_code == 200
    upn = FixtureDirectory("seed/dev.yaml").members_of(dn)[0]
    return await login_wm(upn.split("@")[0])


async def test_get_shows_two_separate_envelopes(login_wm):
    admin = await login_wm("admin")
    r = await put(
        admin,
        "/organizations/1/envelopes",
        {"departments_envelope": 5000000, "systems_envelope": 7000},
    )
    assert r.status_code == 200, r.text
    got = (await admin.get("/organizations/1/envelopes")).json()
    assert Decimal(str(got["departments"]["envelope"])) == 5000000
    assert Decimal(str(got["systems"]["envelope"])) == 7000
    assert got["departments"]["rows"] and "recommendation" not in got["departments"]["rows"][0]
    assert got["systems"]["rows"] == []
    sysres = await admin.post(
        "/organizations/1/systems",
        json={"name": "S", "budget": 1000},
        headers=hdrs(admin),
    )
    assert sysres.status_code == 200, sysres.text
    after = (await admin.get("/organizations/1/envelopes")).json()
    assert Decimal(str(after["systems"]["allocated"])) == 1000
    assert after["departments"]["allocated"] == got["departments"]["allocated"]


async def test_envelope_write_is_admin_only_and_not_below_allocated(login_wm, provisioned):
    admin = await login_wm("admin")
    await put(
        admin,
        "/organizations/1/envelopes",
        {"departments_envelope": 5000000, "systems_envelope": 5000},
    )
    await admin.post(
        "/organizations/1/systems", json={"name": "S", "budget": 4000}, headers=hdrs(admin)
    )
    low = await put(
        admin,
        "/organizations/1/envelopes",
        {"departments_envelope": 5000000, "systems_envelope": 3000},
    )
    assert low.status_code == 422, low.text
    allocator = await allocator_client(login_wm, provisioned)
    denied = await put(
        allocator, "/organizations/1/envelopes", {"departments_envelope": 1, "systems_envelope": 1}
    )
    assert denied.status_code == 403
    assert (await allocator.get("/organizations/1/envelopes")).status_code == 200


async def test_allocator_sets_department_budget_within_envelope(login_wm, provisioned):
    admin = await login_wm("admin")
    rows = (await admin.get("/organizations/1/envelopes")).json()["departments"]
    cap = Decimal(str(rows["allocated"]))
    ok = await put(
        admin,
        "/organizations/1/envelopes",
        {"departments_envelope": float(cap), "systems_envelope": 0},
    )
    assert ok.status_code == 200, ok.text
    dep = rows["rows"][0]
    base = Decimal(str(dep["budget"]))
    allocator = await allocator_client(login_wm, provisioned)
    path = f"/organizations/1/departments/{dep['id']}/budget"
    assert (
        await put(allocator, path, {"budget": float(base - 1), "budget_period": "1mo"})
    ).status_code == 200
    over = await put(allocator, path, {"budget": float(base + 1), "budget_period": "1mo"})
    assert over.status_code == 422, over.text
    async with provisioned() as s:
        assert (await s.get(Department, dep["id"])).budget == base - 1


async def test_department_of_another_organization_is_404(login_wm):
    admin = await login_wm("admin")
    r = await put(
        admin, "/organizations/999/departments/1/budget", {"budget": 1, "budget_period": "1mo"}
    )
    assert r.status_code == 404


async def test_allocator_allocates_org_system_within_systems_envelope(login_wm, provisioned):
    admin = await login_wm("admin")
    await put(
        admin,
        "/organizations/1/envelopes",
        {"departments_envelope": 5000000, "systems_envelope": 5000},
    )
    made = await admin.post(
        "/organizations/1/systems", json={"name": "S", "budget": 1000}, headers=hdrs(admin)
    )
    sid = made.json()["entities"][0]["id"]
    allocator = await allocator_client(login_wm, provisioned)
    path = f"/organizations/1/systems/{sid}/budget"
    assert (await put(allocator, path, {"budget": 5000})).status_code == 200
    assert (await put(allocator, path, {"budget": 5001})).status_code == 422
    assert (
        await put(allocator, "/organizations/1/systems/9999/budget", {"budget": 1})
    ).status_code == 404
