"""SPEC §9.4, §9.5: pending placement lists and the place action."""

import pytest
from lifecycle import A1, A2, DANI, employee, outbox
from sqlalchemy import update

from app.models import Employee
from app.outbox.worker import drain
from app.services.lifecycle import JOINER

PAZIT, LOST = "pazit@corp.example", "lost@corp.example"
C1 = 5  # a D2 group


async def place(client, upn, group_id, reason="placed", key=None, **headers):
    h = {"X-CSRF-Token": client.cookies["wm_csrf"], **headers}
    if reason is not None:
        h["X-Change-Reason"] = reason
    if key:
        h["Idempotency-Key"] = key
    return await client.post(
        f"/pending-placements/{upn}/place", json={"group_id": group_id}, headers=h
    )


async def test_rep_places_pending_of_own_department_then_ready(
    login_wm, provisioned, portal, litellm, prov_settings
):
    r = await place(await login_wm("rep"), PAZIT, A1)
    assert r.status_code == 200, r.text
    assert r.json()["entities"][0]["id"] == PAZIT and r.json()["status"] == "pending"
    emp = await employee(provisioned, PAZIT)
    assert (emp.status, emp.group_id, emp.base_cap) == ("active", A1, 0)
    assert (emp.pending_department_id, emp.pending_reason, emp.pending_since) == (None, None, None)
    rows = await outbox(provisioned, PAZIT)
    assert tuple(x.endpoint for x in rows) == JOINER
    assert rows[1].depends_on == rows[0].id

    assert (await portal.get(PAZIT)).json()["status"] == "provisioning"
    assert (await drain(provisioned, litellm, prov_settings)).failed == 0
    assert (await portal.get(PAZIT)).json()["status"] == "ready"


async def test_rep_place_into_other_department_scope_forbidden(login_wm, provisioned):
    r = await place(await login_wm("rep"), PAZIT, C1)
    assert r.status_code == 422 and r.json()["code"] == "SCOPE_FORBIDDEN"
    assert (await employee(provisioned, PAZIT)).status == "pending_placement"


async def test_rep_cannot_see_unknown_department_404(login_wm, provisioned):
    r = await place(await login_wm("rep"), LOST, A1)
    assert r.status_code == 404
    assert (await employee(provisioned, LOST)).status == "pending_placement"


async def test_admin_places_unknown_department(login_wm, provisioned):
    r = await place(await login_wm("admin"), LOST, C1)
    assert r.status_code == 200, r.text
    assert (await employee(provisioned, LOST)).group_id == C1


@pytest.mark.parametrize("who", ["dana", "pmo", "viewer", "dani"])
async def test_head_pmo_viewer_place_403_or_404(login_wm, provisioned, who):
    r = await place(await login_wm(who), PAZIT, A1)
    assert r.status_code in (403, 404)
    assert (await employee(provisioned, PAZIT)).status == "pending_placement"


async def test_place_non_pending_409(login_wm, provisioned):
    r = await place(await login_wm("admin"), DANI, A2)
    assert r.status_code == 409 and r.json()["code"] == "MEMBER_STATE_CONFLICT"
    assert r.json()["details"] == {"status": "active"}


async def test_admin_replaces_left_employee(login_wm, provisioned):
    async with provisioned() as s:
        await s.execute(update(Employee).where(Employee.upn == DANI).values(status="left"))
        await s.commit()
    assert (await place(await login_wm("rep"), DANI, A2)).status_code in (403, 404, 409)
    r = await place(await login_wm("admin"), DANI, A2)
    assert r.status_code == 200, r.text
    emp = await employee(provisioned, DANI)
    assert (emp.status, emp.group_id, emp.left_at) == ("active", A2, None)


async def test_place_self_forbidden(login_wm, provisioned):
    async with provisioned() as s:
        await s.execute(
            update(Employee).where(Employee.upn == "admin@corp.example").values(
                status="pending_placement", group_id=None
            )
        )  # fmt: skip
        await s.commit()
    r = await place(await login_wm("admin"), "admin@corp.example", A1)
    assert r.status_code == 422 and r.json()["code"] == "SELF_ALLOCATION_FORBIDDEN"


async def test_place_needs_reason_and_replays(login_wm, provisioned):
    admin = await login_wm("admin")
    assert (await place(admin, PAZIT, A1, reason=None)).status_code == 422
    first = await place(admin, PAZIT, A1, key="p-1")
    again = await place(admin, PAZIT, A1, key="p-1")
    assert (first.status_code, again.status_code) == (200, 200)
    assert len(await outbox(provisioned, PAZIT)) == 4


async def test_place_in_progress_409(login_wm, provisioned):
    admin = await login_wm("admin")
    assert (await place(admin, PAZIT, A1)).status_code == 200
    async with provisioned() as s:
        await s.execute(update(Employee).where(Employee.upn == PAZIT).values(status="left"))
        await s.commit()
    r = await place(admin, PAZIT, A2)  # the Joiner rows are still pending
    assert r.status_code == 409 and r.json()["code"] == "LIFECYCLE_IN_PROGRESS"


async def test_pending_list_scoped_to_department(login_wm, provisioned):
    rep, admin = await login_wm("rep"), await login_wm("admin")
    rows = (await rep.get("/departments/1/pending-placements")).json()
    assert [r["upn"] for r in rows] == [PAZIT]
    assert set(rows[0]) >= {"upn", "display_name", "reason", "pending_since"}
    assert rows[0]["reason"] == "no_budget_group"
    assert (await rep.get("/departments/2/pending-placements")).status_code in (403, 404)

    everyone = (await admin.get("/pending-placements")).json()
    assert {r["upn"] for r in everyone} == {PAZIT, LOST}
    unknown = (await admin.get("/pending-placements?department=none")).json()
    assert [r["upn"] for r in unknown] == [LOST]
    one = (await admin.get("/pending-placements?department=1")).json()
    assert [r["upn"] for r in one] == [PAZIT]
    assert (await rep.get("/pending-placements")).status_code == 403


async def test_pending_list_viewer_403(login_wm, provisioned):
    for who in ("viewer", "pmo"):
        client = await login_wm(who)
        assert (await client.get("/departments/1/pending-placements")).status_code == 403
        assert (await client.get("/pending-placements")).status_code == 403
