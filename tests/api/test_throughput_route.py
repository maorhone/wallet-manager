"""Step 5.8: throughput requests and AI-team-only rate limits (D-061, D-065)."""

import pytest

from app.models import Employee
from app.outbox.worker import drain
from tests.api.test_requests import send, submit

DANI = "dani@corp.example"
LIMITS = {"max_parallel": 4, "rpm_limit": 120}


@pytest.fixture
async def ai_team(login_wm, provisioned):
    return await login_wm("aiteam")


async def decide(client, rid, decision, **body):
    return await send(client, "POST", f"/requests/{rid}/decide", {"decision": decision, **body})


async def test_throughput_forwarded_section_then_department_then_ai_team_applies(
    login_wm, ai_team, provisioned, litellm, prov_settings
):
    dani, dana, rep = await login_wm("dani"), await login_wm("dana"), await login_wm("rep")
    rid = await submit(dani, "throughput", payload={"need": "batch jobs", **LIMITS})
    assert (await dana.get("/requests?box=to_handle")).json()["items"][0]["id"] == rid
    assert (await rep.get("/requests?box=to_handle")).json()["items"] == []

    assert (await decide(dana, rid, "forward")).status_code == 200
    got = (await dani.get(f"/requests/{rid}")).json()
    assert (got["status"], got["handler_role"]) == ("forwarded", "department_rep")
    assert [i["id"] for i in (await rep.get("/requests?box=to_handle")).json()["items"]] == [rid]

    assert (await decide(rep, rid, "forward")).status_code == 200
    got = (await dani.get(f"/requests/{rid}")).json()
    assert (got["status"], got["handler_role"]) == ("forwarded", "ai_team")
    assert [i["id"] for i in (await ai_team.get("/requests?box=to_handle")).json()["items"]] == [
        rid
    ]
    assert (await rep.get("/requests?box=to_handle")).json()["items"] == []

    assert (await decide(ai_team, rid, "apply")).status_code == 200
    async with provisioned() as s:
        e = await s.get(Employee, DANI)
        assert (e.max_parallel, e.rpm_limit) == (4, 120)
    await drain(provisioned, litellm, prov_settings)
    assert (await dani.get(f"/requests/{rid}")).json()["status"] == "applied"


async def test_section_head_cannot_reject_throughput(login_wm, ai_team):
    dani, dana, rep = await login_wm("dani"), await login_wm("dana"), await login_wm("rep")
    rid = await submit(dani, "throughput", payload={"need": "x", **LIMITS})
    for who, decision in ((dana, "reject"), (dana, "apply"), (dana, "approve")):
        assert (await decide(who, rid, decision)).status_code in (403, 422)
    assert (await decide(dana, rid, "forward")).status_code == 200
    assert (await decide(rep, rid, "reject")).status_code == 403
    assert (await decide(rep, rid, "apply")).status_code == 403
    assert (await decide(ai_team, rid, "reject")).status_code == 403
    assert (await dani.get(f"/requests/{rid}")).json()["status"] == "forwarded"


async def test_throughput_request_needs_limits(login_wm):
    r = await send(
        await login_wm("dani"),
        "POST",
        "/requests",
        {"type": "throughput", "payload": {"need": "x"}},
    )
    assert r.status_code == 422, r.text


async def test_rep_rate_limit_write_403(login_wm, provisioned):
    rep, dana = await login_wm("rep"), await login_wm("dana")
    for who in (rep, dana):
        r = await send(who, "PUT", f"/groups/1/members/{DANI}/limits", LIMITS)
        assert r.status_code == 403, r.text
        r = await send(who, "PUT", "/groups/1/limits", {"tpm_limit": 100})
        assert r.status_code == 403, r.text


async def test_ai_team_writes_rate_limits(ai_team, provisioned):
    r = await send(ai_team, "PUT", f"/groups/1/members/{DANI}/limits", LIMITS)
    assert r.status_code == 200, r.text
    r = await send(ai_team, "PUT", "/groups/1/limits", {"tpm_limit": 100})
    assert r.status_code == 200, r.text


async def test_ai_team_apply_pending_then_applied(
    login_wm, ai_team, provisioned, litellm, prov_settings, fake_app
):
    dani, dana, rep = await login_wm("dani"), await login_wm("dana"), await login_wm("rep")
    rid = await submit(dani, "throughput", payload={"need": "x", "rpm_limit": 90})
    await decide(dana, rid, "forward")
    await decide(rep, rid, "forward")
    await decide(ai_team, rid, "apply")
    fake_app.state.fake.outage = True
    assert (await drain(provisioned, litellm, prov_settings)).stopped
    assert (await dani.get(f"/requests/{rid}")).json()["status"] == "pending_apply"
    fake_app.state.fake.outage = False
    await drain(provisioned, litellm, prov_settings)
    assert (await dani.get(f"/requests/{rid}")).json()["status"] == "applied"
