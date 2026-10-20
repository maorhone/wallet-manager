import httpx
from grants import create, grant_body, send, when
from sqlalchemy import select

from app.models import Grant, GrantShare
from app.outbox.worker import drain

DANI, DANA, YAEL = "dani@corp.example", "dana@corp.example", "yael@corp.example"
HEAD_DN = "CN=wm-head-a1,OU=Groups,DC=corp,DC=example"


def body(upn=DANI, amount=3000, days=20):
    return [{"upn": upn, "amount": amount, "expires_at": when(days=days)}]


async def share(client, grant_id, rows):
    return await send(client, "POST", f"/grants/{grant_id}/shares", rows, "share")


async def new_grant(login, **kw) -> int:
    r = await create(await login("pmo"), grant_body(**kw))
    assert r.status_code == 200, r.text
    return int(r.json()["entities"][0]["id"])


async def membership(litellm, upn=DANI):
    info = await litellm.team_info("A1")
    return next(m for m in info.team_memberships if m.user_id == upn).litellm_budget_table


async def test_share_writes_temp_fields_after_drain(
    login, provisioned, litellm, prov_settings, seeded
):
    gid = await new_grant(login)
    r = await share(await login("dana"), gid, body())
    assert r.status_code == 200, r.text
    assert [e["entity"] for e in r.json()["entities"]] == ["grant_share", "employee"]
    assert r.json()["status"] == "pending"
    await drain(provisioned, litellm, prov_settings)
    budget = await membership(litellm)
    assert budget.temp_budget_increase == 3000 and budget.temp_budget_expiry is not None
    rows = (await (await login("dana")).get(f"/grants/{gid}/shares")).json()
    assert rows[0]["upn"] == DANI and rows[0]["amount"] == 3000
    assert rows[0]["actions"] == {"edit": True, "close": True} and rows[0]["used"] == 0


async def test_sum_above_grant_share_exceeds_grant(login, provisioned):
    gid = await new_grant(login)
    r = await share(await login("dana"), gid, body(amount=30000) + body(YAEL, 30000))
    assert r.status_code == 422 and r.json()["code"] == "SHARE_EXCEEDS_GRANT"
    assert (r.json()["requested"], r.json()["available"], r.json()["parent_total"]) == (
        60000, 50000, 50000,
    )  # fmt: skip
    async with provisioned() as s:  # all-or-nothing
        assert not (await s.scalars(select(GrantShare))).all()


async def test_expiry_after_grant_422(login, provisioned):
    gid = await new_grant(login)
    r = await share(await login("dana"), gid, body(days=40))
    assert r.status_code == 422 and r.json()["code"] == "SHARE_EXPIRY_AFTER_GRANT"
    r = await share(await login("dana"), gid, body(days=-1))
    assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"


async def test_head_shares_to_self_self_allocation_forbidden(login, provisioned):
    gid = await new_grant(login)
    r = await share(await login("dana"), gid, body(DANA))
    assert r.status_code == 422 and r.json()["code"] == "SELF_ALLOCATION_FORBIDDEN"
    assert r.json()["details"]["owner_role"] == "department_rep"
    assert r.json()["details"]["rows"][0]["upn"] == DANA


async def test_rep_shares_to_group_head_ok(login, provisioned):
    gid = await new_grant(login)
    r = await share(await login("rep"), gid, body(DANA))
    assert r.status_code == 200, r.text


async def test_head_to_cohead_scope_forbidden(login, provisioned):
    gid = await new_grant(login)
    client = await login("dana")
    client._transport.app.state.directory.users[YAEL]["groups"].append(HEAD_DN)
    r = await share(client, gid, body(YAEL))
    assert r.status_code == 422 and r.json()["code"] == "SCOPE_FORBIDDEN"


async def test_upn_outside_group_scope_forbidden(login, provisioned):
    gid = await new_grant(login)
    r = await share(await login("dana"), gid, body("avi@corp.example"))  # member of A2
    assert r.status_code == 422 and r.json()["code"] == "SCOPE_FORBIDDEN"


async def test_second_active_share_same_upn_invalid(login, provisioned):
    gid = await new_grant(login)
    dana = await login("dana")
    assert (await share(dana, gid, body())).status_code == 200
    r = await share(dana, gid, body(amount=10))
    assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"
    assert r.json()["details"]["reason"] == "share_exists"


async def test_blocked_member_conflict_and_patch(login, provisioned):
    gid = await new_grant(login)
    dana = await login("dana")
    r = await share(dana, gid, body())
    sid = int(r.json()["entities"][0]["id"])
    r = await send(dana, "PATCH", f"/grants/{gid}/shares/{sid}", {"amount": 4000}, "more")
    assert r.status_code == 200, r.text
    r = await send(dana, "PATCH", f"/grants/{gid}/shares/{sid}", {"amount": 60000}, "more")
    assert r.json()["code"] == "SHARE_EXCEEDS_GRANT"
    async with provisioned() as s:
        (await s.get(GrantShare, sid)).status = "closed"
        (await s.get(GrantShare, sid)).closed_reason = "manual"
        await s.commit()
    r = await send(dana, "PATCH", f"/grants/{gid}/shares/{sid}", {"amount": 10}, "more")
    assert r.status_code == 422


async def test_viewer_pmo_cannot_share_403(login, provisioned):
    gid = await new_grant(login)
    for who in ("pmo", "viewer"):
        r = await share(await login(who), gid, body())
        assert r.status_code in (403, 404), who
    assert (await share(await login("pmo"), gid, body())).status_code == 403
    assert (await share(await login("dani"), gid, body())).status_code in (403, 404)


async def _charge(fake_app, upn, amount):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as c:
        r = await c.post(
            "/_test/spend", json={"team_id": "A1", "user_id": upn, "model": "m", "amount": amount}
        )
        assert r.status_code == 200, r.text


async def test_close_share_used_returned_pool_and_team_lowered(
    login_wm, provisioned, litellm, prov_settings, fake_app
):
    gid = await new_grant(login_wm)
    dana = await login_wm("dana")
    sid = int((await share(dana, gid, body())).json()["entities"][0]["id"])
    await drain(provisioned, litellm, prov_settings)
    before = (await litellm.team_info("A1")).team_info.max_budget
    await _charge(fake_app, DANI, 16000)  # base cap 15000 -> 1000 lands on the share
    r = await send(dana, "POST", f"/grants/{gid}/shares/{sid}/close", {"reason": "done"}, "close")
    assert r.status_code == 200, r.text
    async with provisioned() as s:
        sh = await s.get(GrantShare, sid)
        g = await s.get(Grant, gid)
        assert (sh.status, sh.used, sh.returned, sh.closed_reason) == (
            "closed",
            1000,
            2000,
            "manual",
        )
        assert g.returned == 2000
    await drain(provisioned, litellm, prov_settings)
    assert (await litellm.team_info("A1")).team_info.max_budget == before - 2000
    budget = await membership(litellm)
    assert not budget.temp_budget_increase and budget.temp_budget_expiry is None
    pool = (await (await login_wm("pmo")).get("/departments/1/pmo-pool")).json()
    assert pool["available"] == 180000 - 48000
    again = await send(dana, "POST", f"/grants/{gid}/shares/{sid}/close", {}, "close")
    assert again.status_code == 422


async def test_close_grant_closes_shares_then_returns_remainder(
    login_wm, provisioned, litellm, prov_settings, fake_app
):
    gid = await new_grant(login_wm)
    dana = await login_wm("dana")
    assert (await share(dana, gid, body(amount=3000) + body(YAEL, 2000))).status_code == 200
    await drain(provisioned, litellm, prov_settings)
    await _charge(fake_app, DANI, 15500)
    r = await send(await login_wm("pmo"), "POST", f"/grants/{gid}/close", None, "end")
    assert r.status_code == 200, r.text
    async with provisioned() as s:
        shares = (await s.scalars(select(GrantShare).order_by(GrantShare.id))).all()
        g = await s.get(Grant, gid)
    assert [(x.status, x.used) for x in shares] == [("closed", 500), ("closed", 0)]
    assert (g.used, g.returned, g.status) == (500, 49500, "closed")
    await drain(provisioned, litellm, prov_settings)
    assert not (await membership(litellm, YAEL)).temp_budget_increase
