import asyncio
from decimal import Decimal as D

import pytest
from grants import create, grant_body, send, when
from sqlalchemy import func, select
from test_shares import DANA, DANI, HEAD_DN, YAEL, _charge, body, new_grant, share

from app.models import Grant, GrantShare, SpendSnapshot
from app.outbox.worker import drain


@pytest.mark.parametrize(
    "actor,target,code",
    [
        ("dana", DANA, "SELF_ALLOCATION_FORBIDDEN"),
        ("dana", YAEL, "SCOPE_FORBIDDEN"),
        ("rep", YAEL, "SCOPE_FORBIDDEN"),
    ],
)
async def test_review_close_target_authorization(login_wm, provisioned, actor, target, code):
    gid = await new_grant(login_wm)
    admin = await login_wm("admin")
    sid = int((await share(admin, gid, body(target))).json()["entities"][0]["id"])
    client = await login_wm(actor)
    directory = client._transport.app.state.directory
    if target == YAEL:
        directory.users[YAEL]["groups"].append(
            HEAD_DN if actor == "dana" else "CN=wm-rep-d1,OU=Groups,DC=corp,DC=example"
        )
    r = await send(client, "POST", f"/grants/{gid}/shares/{sid}/close", {})
    assert r.status_code == 422 and r.json()["code"] == code


async def test_review_reverse_close_conserves_usage(
    login_wm, provisioned, litellm, prov_settings, fake_app
):
    ids = []
    dana = await login_wm("dana")
    for days in (10, 20):
        gid = await new_grant(login_wm)
        sid = int((await share(dana, gid, body(amount=100, days=days))).json()["entities"][0]["id"])
        ids.append((gid, sid))
    await drain(provisioned, litellm, prov_settings)
    await _charge(fake_app, DANI, 15050)
    for gid, sid in reversed(ids):
        assert (
            await send(dana, "POST", f"/grants/{gid}/shares/{sid}/close", {})
        ).status_code == 200
    async with provisioned() as db:
        rows = [await db.get(GrantShare, sid) for _, sid in ids]
        assert [s.used for s in rows] == [D(50), D(0)]
        assert sum(s.used + s.returned for s in rows) == D(200)


async def test_review_fractional_return_conservation(
    login_wm, provisioned, litellm, prov_settings, fake_app
):
    gid = await new_grant(login_wm)
    dana = await login_wm("dana")
    sid = int((await share(dana, gid, body(amount=1))).json()["entities"][0]["id"])
    await drain(provisioned, litellm, prov_settings)
    await _charge(fake_app, DANI, "15000.0040")
    assert (await send(dana, "POST", f"/grants/{gid}/shares/{sid}/close", {})).status_code == 200
    async with provisioned() as db:
        s, g = await db.get(GrantShare, sid), await db.get(Grant, gid)
        assert s.used == D("0.0040")
        assert s.used + s.returned == s.amount
        assert g.returned == s.returned


async def test_review_closed_grant_retains_spent_team_budget(
    login_wm, provisioned, litellm, prov_settings, fake_app
):
    gid = await new_grant(login_wm, amount=100, model="m")
    dana = await login_wm("dana")
    await share(dana, gid, body(amount=100))
    await drain(provisioned, litellm, prov_settings)
    before = (await litellm.team_info("A1")).team_info.max_budget
    await _charge(fake_app, DANI, 15040)
    assert (
        await send(await login_wm("pmo"), "POST", f"/grants/{gid}/close", {})
    ).status_code == 200
    await drain(provisioned, litellm, prov_settings)
    info = (await litellm.team_info("A1")).team_info
    assert D(str(info.max_budget)) == D(str(before)) - D(60)
    assert D(str(info.model_max_budget["m"]["budget_limit"])) == D(40)
    summary = (await dana.get("/groups/1/summary")).json()
    assert D(str(summary["wallet"]["grants"])) == D(40)
    assert next(p for p in summary["pots"] if p["model"] == "m")["amount"] == 40


async def test_review_last_model_pot_cleared(login_wm, provisioned, litellm, prov_settings):
    gid = await new_grant(login_wm, model="m")
    await drain(provisioned, litellm, prov_settings)
    assert (
        await send(await login_wm("pmo"), "POST", f"/grants/{gid}/close", {})
    ).status_code == 200
    await drain(provisioned, litellm, prov_settings)
    assert "m" not in ((await litellm.team_info("A1")).team_info.model_max_budget or {})


async def test_review_closed_spend_only_in_local_closing_month(login_wm, provisioned):
    from datetime import UTC, datetime

    from app.services.views import _grants_by_group

    gid = await new_grant(login_wm, amount=100)
    async with provisioned() as db:
        g = await db.get(Grant, gid)
        g.status, g.used, g.returned = "closed", D(40), D(60)
        g.closed_at = datetime(2026, 9, 30, 21, 1, tzinfo=UTC)
        await db.commit()
        assert (await _grants_by_group(db, [1], "Asia/Jerusalem", "2026-10"))[1] == D(40)
        assert (await _grants_by_group(db, [1], "Asia/Jerusalem", "2026-11")).get(1, D(0)) == D(0)
        assert (await _grants_by_group(db, [1], "UTC", "2026-10")).get(1, D(0)) == D(0)


async def test_review_concurrent_pending_retry(login_wm, provisioned):
    client = await login_wm("pmo")
    payload = grant_body(starts_at=when(days=2))
    results = await asyncio.gather(
        *[create(client, payload, **{"Idempotency-Key": "pending-retry"}) for _ in range(2)]
    )
    assert [r.status_code for r in results] == [200, 200]
    async with provisioned() as db:
        assert await db.scalar(select(func.count()).select_from(Grant)) == 1


async def test_review_display_closed_usage_only_once(login_wm, provisioned):
    from app.services.attribution import displayed_used

    gid = await new_grant(login_wm)
    async with provisioned() as db:
        g = await db.get(Grant, gid)
        first = GrantShare(
            grant_id=gid,
            upn=DANI,
            amount=100,
            expires_at=g.expires_at,
            status="closed",
            closed_reason="manual",
            used=50,
            returned=50,
        )
        db.add(first)
        await db.flush()
        second = GrantShare(grant_id=gid, upn=DANI, amount=100, expires_at=g.expires_at)
        db.add(second)
        await db.commit()
        shown = await displayed_used(db, DANI, D(15000), D(15050))
        assert shown[first.id] == D(50) and shown[second.id] == D(0)


async def test_review_underuse_closed_and_recreated(login_wm, provisioned):
    from app.services.notify_scan import _grant_used
    from app.services.spend import Spend

    gid = await new_grant(login_wm, amount=1000)
    async with provisioned() as db:
        g = await db.get(Grant, gid)
        db.add_all(
            [
                GrantShare(
                    grant_id=gid,
                    upn=DANI,
                    amount=100,
                    expires_at=g.expires_at,
                    status="closed",
                    closed_reason="manual",
                    used=100,
                ),
                GrantShare(grant_id=gid, upn=DANI, amount=100, expires_at=g.expires_at),
            ]
        )
        await db.commit()
        snapshot = SpendSnapshot(
            scope_kind="employee", scope_id=DANI, model=None, period="2026-10", spend=D(15100)
        )
        assert await _grant_used(db, g, Spend([snapshot], "2026-10")) == D(100)


async def test_review_active_usage_reports(
    login_wm, provisioned, litellm, prov_settings, fake_app, snapshot
):
    gid = await new_grant(login_wm)
    await share(await login_wm("dana"), gid, body())
    await drain(provisioned, litellm, prov_settings)
    await _charge(fake_app, DANI, 16000)
    await snapshot([("employee", DANI, None, D(16000))])
    pmo = await login_wm("pmo")
    assert (await pmo.get("/grants")).json()[0]["used"] == 1000
    assert (await pmo.get("/projects/PRJ-1/summary")).json()["totals"]["used"] == 1000
    assert (await pmo.get("/departments/1/pmo-pool")).json()["used"] == 1000


async def test_review_move_then_new_group_attribution(
    login_wm, provisioned, litellm, prov_settings, fake_app
):
    from lifecycle import act

    gid = await new_grant(login_wm)
    await share(await login_wm("dana"), gid, body(amount=100))
    await drain(provisioned, litellm, prov_settings)
    await _charge(fake_app, DANI, 15050)
    assert (
        await act(await login_wm("rep"), 1, DANI, "move", {"to_group_id": 2})
    ).status_code == 200
    await drain(provisioned, litellm, prov_settings)
    new = await new_grant(login_wm, group_id=2)
    rep = await login_wm("rep")
    sid = int((await share(rep, new, body(amount=100))).json()["entities"][0]["id"])
    await drain(provisioned, litellm, prov_settings)
    # The new group's default base cap is zero; new membership spend is 50.
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as c:
        assert (
            await c.post(
                "/_test/spend", json={"team_id": "A2", "user_id": DANI, "model": "m", "amount": 50}
            )
        ).status_code == 200
    assert (await send(rep, "POST", f"/grants/{new}/shares/{sid}/close", {})).status_code == 200
    async with provisioned() as db:
        s = await db.get(GrantShare, sid)
        assert s.used == D(50)
        assert s.used + s.returned == s.amount
    # Returning to the original team starts another membership; its old shares cannot absorb spend.
    await drain(provisioned, litellm, prov_settings)
    assert (await act(rep, 2, DANI, "move", {"to_group_id": 1})).status_code == 200
    await drain(provisioned, litellm, prov_settings)
    back = await new_grant(login_wm)
    back_sid = int((await share(rep, back, body(amount=100))).json()["entities"][0]["id"])
    await drain(provisioned, litellm, prov_settings)
    await _charge(fake_app, DANI, 50)
    assert (
        await send(rep, "POST", f"/grants/{back}/shares/{back_sid}/close", {})
    ).status_code == 200
    async with provisioned() as db:
        s = await db.get(GrantShare, back_sid)
        assert s.used == D(50)
        assert s.used + s.returned == s.amount
