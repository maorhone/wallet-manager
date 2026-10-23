"""Step 5.7: requests center backend, credits and new initiative (D-044, D-068, D-074)."""

import uuid

from sqlalchemy import select

from app.models import Employee, Initiative, Request
from app.outbox.worker import drain

A1 = 1  # dana heads it; dani and yael are members


async def send(client, method, path, body=None):
    headers = {
        "X-CSRF-Token": client.cookies["wm_csrf"],
        "X-Change-Reason": "request",
        "Idempotency-Key": uuid.uuid4().hex,
    }
    return await client.request(method, path, json=body, headers=headers)


async def submit(client, type="credits", **changes):
    body = {"type": type, "payload": {"need": "long refactor"}, **changes}
    r = await send(client, "POST", "/requests", body)
    assert r.status_code == 200, r.text
    return int(r.json()["entities"][0]["id"])


async def test_employee_requests_credits_section_head_approves_cap_raised(
    login_wm, provisioned, litellm, prov_settings
):
    dani, dana = await login_wm("dani"), await login_wm("dana")
    rid = await submit(dani, amount=500)
    assert [i["id"] for i in (await dani.get("/requests?box=mine")).json()["items"]] == [rid]
    assert [i["id"] for i in (await dana.get("/requests?box=to_handle")).json()["items"]] == [rid]
    assert (await dani.get("/requests?box=to_handle")).json()["items"] == []
    denied = await send(dani, "POST", f"/requests/{rid}/decide", {"decision": "approve"})
    assert denied.status_code == 403, denied.text
    r = await send(dana, "POST", f"/requests/{rid}/decide", {"decision": "approve", "amount": 700})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"
    assert (await dani.get(f"/requests/{rid}")).json()["status"] == "pending_apply"
    await drain(provisioned, litellm, prov_settings)
    got = (await dani.get(f"/requests/{rid}")).json()
    assert (got["status"], got["amount"]) == ("applied", 700)
    async with provisioned() as s:
        assert (await s.get(Employee, "dani@corp.example")).base_cap == 15700
    again = await send(dana, "POST", f"/requests/{rid}/decide", {"decision": "reject"})
    assert again.status_code == 409, again.text


async def test_credits_over_allocatable_422(login_wm):
    dani, dana = await login_wm("dani"), await login_wm("dana")
    rid = await submit(dani)
    r = await send(
        dana, "POST", f"/requests/{rid}/decide", {"decision": "approve", "amount": 10**8}
    )
    assert r.status_code == 422 and r.json()["code"] == "PARENT_OVERALLOCATED", r.text
    no_amount = await send(dana, "POST", f"/requests/{rid}/decide", {"decision": "approve"})
    assert no_amount.status_code == 422, no_amount.text


async def test_initiative_request_approved_creates_initiative(login_wm, provisioned):
    dani, dana = await login_wm("dani"), await login_wm("dana")
    rid = await submit(
        dani, "initiative", amount=2000, payload={"name": "Pilot", "need": "new bot"}
    )
    r = await send(dana, "POST", f"/requests/{rid}/decide", {"decision": "approve", "amount": 1500})
    assert r.status_code == 200, r.text
    async with provisioned() as s:
        row = (await s.scalars(select(Initiative))).one()
        assert (row.name, row.budget, row.owner_upn, row.group_id) == (
            "Pilot", 1500, "dani@corp.example", A1,
        )  # fmt: skip
    assert (await dani.get(f"/requests/{rid}")).json()["status"] == "applied"


async def test_reject_notifies_requester(login_wm):
    dani, dana = await login_wm("dani"), await login_wm("dana")
    rid = await submit(dani)
    r = await send(dana, "POST", f"/requests/{rid}/decide", {"decision": "reject"})
    assert r.status_code == 200, r.text
    assert (await dani.get(f"/requests/{rid}")).json()["status"] == "rejected"
    items = (await dani.get("/me/notifications")).json()["items"]
    assert [n["kind"] for n in items if n["kind"].startswith("request_")] == ["request_rejected"]


async def test_filters_by_type_and_status(login_wm):
    dani, dana = await login_wm("dani"), await login_wm("dana")
    credits = await submit(dani, amount=100)
    init = await submit(dani, "initiative", payload={"name": "X", "need": "new tool"})
    await send(dana, "POST", f"/requests/{credits}/decide", {"decision": "reject"})
    ids = lambda r: [i["id"] for i in r.json()["items"]]  # noqa: E731
    assert ids(await dani.get("/requests?box=mine&type=initiative")) == [init]
    assert ids(await dani.get("/requests?box=mine&status=rejected")) == [credits]
    assert ids(await dana.get("/requests?box=to_handle&status=submitted")) == [init]
    assert (await dani.get("/requests?box=mine&type=bogus")).status_code == 422


async def test_other_section_head_403(login_wm):
    dani, noa = await login_wm("dani"), await login_wm("noa")
    rid = await submit(dani, amount=100)
    r = await send(noa, "POST", f"/requests/{rid}/decide", {"decision": "approve", "amount": 100})
    assert r.status_code == 403, r.text
    assert (await noa.get(f"/requests/{rid}")).status_code == 404
    assert (await noa.get("/requests?box=to_handle")).json()["items"] == []


async def test_request_requires_short_need(login_wm):
    dani = await login_wm("dani")
    for payload in ({}, {"need": "  "}, {"need": 42}, {"need": "x" * 1001}):
        r = await send(dani, "POST", "/requests", {"type": "credits", "payload": payload})
        assert r.status_code == 422, r.text


async def test_head_request_routes_to_rep_and_cannot_self_approve(login_wm, provisioned):
    dana, rep = await login_wm("dana"), await login_wm("rep")
    rid = await submit(dana, amount=100)
    assert (await dana.get(f"/requests/{rid}")).json()["handler_role"] == "department_rep"
    assert [r["id"] for r in (await rep.get("/requests?box=to_handle")).json()["items"]] == [rid]
    denied = await send(dana, "POST", f"/requests/{rid}/decide", {"decision": "approve"})
    assert denied.status_code == 403, denied.text
    async with provisioned() as s:
        assert (await s.get(Employee, "dana@corp.example")).base_cap == 15000
    approved = await send(rep, "POST", f"/requests/{rid}/decide", {"decision": "approve"})
    assert approved.status_code == 200, approved.text


async def test_head_cannot_approve_newly_promoted_cohead(login_wm, provisioned):
    yael, dana = await login_wm("yael"), await login_wm("dana")
    rid = await submit(yael, amount=100)
    directory = dana._transport.app.state.directory
    directory.users["yael@corp.example"]["groups"].append(
        "CN=wm-head-a1,OU=Groups,DC=corp,DC=example"
    )
    denied = await send(dana, "POST", f"/requests/{rid}/decide", {"decision": "approve"})
    assert denied.status_code == 422 and denied.json()["code"] == "SCOPE_FORBIDDEN", denied.text
    async with provisioned() as s:
        assert (await s.get(Employee, "yael@corp.example")).base_cap == 15000


async def test_credit_application_pending_during_outage_then_applied(
    login_wm, provisioned, litellm, prov_settings, fake_app
):
    dani, dana = await login_wm("dani"), await login_wm("dana")
    rid = await submit(dani, amount=100)
    await send(dana, "POST", f"/requests/{rid}/decide", {"decision": "approve"})
    fake_app.state.fake.outage = True
    assert (await drain(provisioned, litellm, prov_settings)).stopped
    assert (await dani.get(f"/requests/{rid}")).json()["status"] == "pending_apply"
    fake_app.state.fake.outage = False
    await drain(provisioned, litellm, prov_settings)
    assert (await dani.get(f"/requests/{rid}")).json()["status"] == "applied"


async def test_credit_application_permanent_failure_is_visible(
    login_wm, provisioned, litellm, prov_settings, fake_app
):
    dani, dana = await login_wm("dani"), await login_wm("dana")
    rid = await submit(dani, amount=100)
    await send(dana, "POST", f"/requests/{rid}/decide", {"decision": "approve"})
    fake_app.state.fake.failures["/team/member_update"] = [400, 1]
    assert (await drain(provisioned, litellm, prov_settings)).failed == 1
    assert (await dani.get(f"/requests/{rid}")).json()["status"] == "failed"
    assert [i["id"] for i in (await dani.get("/requests?status=failed")).json()["items"]] == [rid]
    async with provisioned() as s:
        write_id = (await s.get(Request, rid)).application_write_id
    admin = await login_wm("admin")
    retried = await send(admin, "POST", f"/outbox/{write_id}/retry")
    assert retried.status_code == 200, retried.text
    assert (await dani.get(f"/requests/{rid}")).json()["status"] == "pending_apply"
    await drain(provisioned, litellm, prov_settings)
    assert (await dani.get(f"/requests/{rid}")).json()["status"] == "applied"
