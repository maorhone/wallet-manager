"""Review round 6 (round-5 re-review): ownership, /me and credential-action fixes."""

import pytest
from sqlalchemy import select

from app.models import ProductionSystem
from tests.api.test_internal_systems import D1, make_org, send
from tests.api.test_review_money import DANA, _initiative
from tests.api.test_service_keys import body, post

pytestmark = pytest.mark.usefixtures("wide_envelope")


# review-p5-r5 blockers/majors (production:108, schemas/production:14, me:99): owner assignment
async def test_r6_internal_system_owner_assigned_and_listed_as_role(login_wm, provisioned):
    admin = await login_wm("admin")
    await make_org(admin)
    body = {"name": "S", "budget": 100, "managing_group_id": 1, "owner_upn": "dani@corp.example"}
    r = await send(admin, "POST", f"/production/departments/{D1}/systems", body)
    assert r.status_code == 200, r.text
    async with provisioned() as s:
        sysrow = (await s.scalars(select(ProductionSystem))).one()
        assert sysrow.owner_upn == "dani@corp.example"
    r = await send(
        admin, "PATCH", f"/production/systems/{sysrow.id}", {"budget": 100, "owner_upn": DANA}
    )
    assert r.status_code == 200, r.text
    bad = await send(
        admin, "PATCH", f"/production/systems/{sysrow.id}", {"budget": 100, "owner_upn": "x@y"}
    )
    assert bad.status_code == 422
    me = (await (await login_wm("dana")).get("/me")).json()
    assert "system_owner" in [r["role"] for r in me["roles"]]


# review-p5-r5 major (me:65): missing telemetry keeps the wallet and says so
async def test_r6_me_status_without_telemetry(login, provisioned):
    client = await login("dani", litellm_database_url="")
    r = await client.get("/me/status")
    assert r.status_code == 200, r.text
    assert r.json()["blocks_available"] is False and r.json()["last_blocks"] == []


# review-p5-r5 major (permissions:328): a head who owns the initiative keeps the key actions
async def test_r6_head_owner_keeps_rotate_revoke(login_wm, provisioned):
    iid = await _initiative(provisioned, owner=DANA)
    head = await login_wm("dana")
    r = await post(head, payload=body(owner_kind="initiative", owner_id=iid))
    assert r.status_code == 200, r.text
    listed = await head.get(f"/service-keys?owner_kind=initiative&owner_id={iid}")
    assert listed.json()["items"][0]["actions"] == {"rotate": True, "revoke": True}
