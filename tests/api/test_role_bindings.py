"""§11.1 GET|PUT /role-bindings, §6 Actors and permissions."""

import pytest
from sqlalchemy import select

from app.models import AuditLog, RoleBinding

D1 = 1


async def test_replace_department_rep_binding_takes_effect_on_next_login(
    login_wm, provisioned, put
):
    before = await login_wm("rep")
    assert (await before.get(f"/departments/{D1}/summary")).status_code == 200

    admin = await login_wm("admin")
    new_dn = "CN=wm-rep-d1-v2,OU=Groups,DC=corp,DC=example"
    body = {
        "scope_kind": "department", "scope_id": D1, "role": "department_rep",
        "ad_group_dns": [new_dn],
    }  # fmt: skip
    r = await put(admin, "/role-bindings", body)
    assert r.status_code == 200, r.text

    got = (await admin.get(f"/role-bindings?scope_kind=department&scope_id={D1}")).json()
    row = next(x for x in got if x["role"] == "department_rep")
    assert row["ad_group_dns"] == [new_dn]

    assert (await before.get(f"/departments/{D1}/summary")).status_code == 404

    after = await login_wm("rep")
    assert (await after.get(f"/departments/{D1}/summary")).status_code == 404


async def test_last_admin_binding_cannot_be_removed(login_wm, provisioned, put):
    admin = await login_wm("admin")
    r = await put(
        admin, "/role-bindings",
        {"scope_kind": "global", "scope_id": None, "role": "platform_admin", "ad_group_dns": []},
    )  # fmt: skip
    assert r.status_code == 422
    assert r.json()["code"] == "INVALID_REQUEST"


async def test_role_not_allowed_for_scope_invalid(login_wm, provisioned, put):
    admin = await login_wm("admin")
    r = await put(
        admin, "/role-bindings",
        {
            "scope_kind": "department", "scope_id": D1, "role": "platform_admin",
            "ad_group_dns": ["CN=x,OU=Groups,DC=corp,DC=example"],
        },
    )  # fmt: skip
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "INVALID_REQUEST"
    assert body["details"]["field"] == "role"


@pytest.mark.parametrize("role", ["platform_admin", "viewer"])
async def test_global_binding_replacement_and_removal(login, seeded, put, role):
    admin = await login("admin")
    before = (await admin.get("/role-bindings?scope_kind=global")).json()
    old_dns = next((r["ad_group_dns"] for r in before if r["role"] == role), [])
    if not old_dns:
        async with seeded() as s:
            s.add(
                RoleBinding(
                    ad_group_dn="CN=old-viewer,DC=corp,DC=example",
                    role=role,
                    scope_kind="global",
                    scope_id=None,
                )
            )
            await s.commit()
        old_dns = ["CN=old-viewer,DC=corp,DC=example"]
    new_dns = ["CN=new-admin,DC=corp,DC=example"] if role == "platform_admin" else []
    r = await put(
        admin,
        "/role-bindings",
        {
            "scope_kind": "global",
            "scope_id": None,
            "role": role,
            "ad_group_dns": new_dns,
        },
    )
    assert r.status_code == 200, r.text
    async with seeded() as s:
        rows = list(
            await s.scalars(
                select(RoleBinding).where(
                    RoleBinding.role == role,
                    RoleBinding.scope_kind == "global",
                    RoleBinding.scope_id.is_(None),
                )
            )
        )
        assert sorted(row.ad_group_dn for row in rows) == new_dns
        audit = (
            await s.scalars(
                select(AuditLog).where(
                    AuditLog.request_id == r.json()["request_id"],
                )
            )
        ).one()
        assert audit.before == {"ad_group_dns": sorted(old_dns)}
        assert audit.after == {"ad_group_dns": new_dns}
    if role == "platform_admin":
        assert (await admin.get("/role-bindings?scope_kind=global")).status_code == 403
