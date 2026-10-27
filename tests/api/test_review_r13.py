"""Phase 5 round-12 re-review (fixed in round 13): regression tests."""

from sqlalchemy import select

from app.litellm_client import LiteLLMReader
from app.litellm_client.errors import LiteLLMPermanentError
from app.models import Grp, LitellmWrite, ServiceKey, Transfer
from app.outbox.worker import drain
from tests.api.test_review_money import DANI, YAEL, _log
from tests.api.test_review_r10 import _quiet_group
from tests.api.test_transfers import move, send

REP_HEADERS = {"X-Change-Reason": "split"}


async def _split(client, groups):
    body = {"groups": groups, "pmo_pool": 180000, "reserve": 50000}
    headers = {**REP_HEADERS, "X-CSRF-Token": client.cookies["wm_csrf"]}
    return await client.put("/departments/1/split", json=body, headers=headers)


# review-p5-r12 blocker B1b: a split cut cannot release credit a departed member already consumed
async def test_r13_split_cut_keeps_consumed_credit(login_wm, provisioned, fake_engine):
    async with provisioned() as s:
        await _quiet_group(s, budget=100)
        s.add(ServiceKey(owner_kind="group", owner_id=1, max_budget=20, status="active"))
        await s.commit()
    await _log(fake_engine, 1, "A1", "ghost@corp.example", 80)
    rep = await login_wm("rep")
    r = await _split(rep, [{"group_id": 1, "base": 20}])
    assert r.status_code == 422, r.text
    assert r.json()["details"]["reason"] == "below_consumed"
    r = await _split(rep, [{"group_id": 1, "base": 100}])
    assert r.status_code == 200, r.text
    async with provisioned() as s:
        assert (await s.scalar(select(Grp.base_budget).where(Grp.id == 1))) == 100


# review-p5-r12 blocker B1b: with no fresh spend reading a cut is refused, never assumed zero
async def test_r13_split_cut_refused_without_spend_source(login, provisioned):
    async with provisioned() as s:
        await _quiet_group(s, budget=100)
        await s.commit()
    rep = await login("rep", litellm_database_url=None)
    r = await _split(rep, [{"group_id": 1, "base": 20}])
    assert r.status_code == 503, r.text


# review-p5-r12 major B1a1: a failed destination write is settled by the sibling that published it
async def test_r13_failed_destination_write_superseded_by_sibling(
    login_wm, provisioned, litellm, prov_settings, ro_engine
):
    head = await login_wm("dana")
    for amount in (10, 20):
        assert (await send(head, "POST", "/transfers", move(amount, DANI, YAEL))).status_code == 200
    real, calls = litellm.team_member_update, []

    async def flaky(body):
        if body.user_id == YAEL:
            calls.append(body)
            if len(calls) == 1:
                raise LiteLLMPermanentError(400, "injected")
        return await real(body)

    litellm.team_member_update = flaky
    reader = LiteLLMReader(ro_engine)
    await drain(provisioned, litellm, prov_settings, reader)
    litellm.team_member_update = real
    await drain(provisioned, litellm, prov_settings, reader)
    async with provisioned() as s:
        assert set(await s.scalars(select(Transfer.state))) == {"applied"}
        statuses = set(await s.scalars(select(LitellmWrite.status)))
        assert "failed" not in statuses


async def _ownerless_group_system(provisioned, budget=100):
    from app.models import Department, ProductionSystem

    async with provisioned() as s:
        (await s.get(Department, 1)).budget += 1000  # D1 is exactly full in the seed
        row = ProductionSystem(
            department_id=1, name="NoGroup", budget=budget, owner_upn=YAEL, litellm_team_id="sys-ng"
        )
        s.add(row)
        await s.commit()
        return row.id


# review-p5-r12 major B1c: an owner of a system with no managing section asks the department
async def test_r13_owner_credit_request_without_managing_group(
    login_wm, provisioned, litellm, prov_settings
):
    from app.models import ProductionSystem, Request
    from tests.api.test_requests import send as rsend
    from tests.api.test_requests import submit

    sid = await _ownerless_group_system(provisioned)
    yael, dana, rep = await login_wm("yael"), await login_wm("dana"), await login_wm("rep")
    target = {"kind": "system", "id": sid}
    rid = await submit(yael, "credits", amount=50, target=target)
    async with provisioned() as s:
        req = await s.get(Request, rid)
        assert (req.scope_kind, req.scope_id, req.handler_role) == (
            "department",
            1,
            "department_rep",
        )
    assert (
        await rsend(dana, "POST", f"/requests/{rid}/decide", {"decision": "approve"})
    ).status_code in (403, 404)
    r = await rsend(rep, "POST", f"/requests/{rid}/decide", {"decision": "approve"})
    assert r.status_code == 200, r.text
    async with provisioned() as s:
        assert (await s.get(ProductionSystem, sid)).budget == 150


# review-p5-r12 major B1c: a throughput request for such a system goes REP -> AI team
async def test_r13_owner_throughput_request_without_managing_group(login_wm, provisioned):
    from app.models import Request
    from tests.api.test_requests import send as rsend
    from tests.api.test_requests import submit

    sid = await _ownerless_group_system(provisioned)
    yael, rep, ai = await login_wm("yael"), await login_wm("rep"), await login_wm("aiteam")
    rid = await submit(
        yael, "throughput", payload={"need": "more"}, target={"kind": "system", "id": sid}
    )
    r = await rsend(rep, "POST", f"/requests/{rid}/decide", {"decision": "forward"})
    assert r.status_code == 200, r.text
    r = await rsend(ai, "POST", f"/requests/{rid}/decide", {"decision": "apply", "max_parallel": 4})
    assert r.status_code == 200, r.text
    async with provisioned() as s:
        assert (await s.get(Request, rid)).status == "applied"


# review-p5-r12 major B3: request increases and transfers form one chronological budget history
async def test_r13_initiative_budget_history_merges_request_increases_and_transfers(provisioned):
    from datetime import UTC, datetime, timedelta

    from app.models import Initiative
    from app.services.history import initiative_budgets_as_of
    from tests.api.test_review_r8 import _audit

    now = datetime.now(UTC)
    async with provisioned() as s:
        ini = Initiative(
            name="H", owner_upn=YAEL, group_id=1, department_id=1, budget=300, created_by=YAEL,
            created_at=now - timedelta(days=90),
        )  # fmt: skip
        s.add(ini)
        await s.flush()
        iid = ini.id
        # 100 -> (transfer) 150 at -40d -> (approved request) 200 at -20d -> (transfer) 300 now
        await _audit(s, "transfer_initiative", iid, {"total": 100}, {"total": 150}, now - timedelta(days=40))
        await _audit(s, "initiative", iid, {"budget": 150}, {"budget": 200}, now - timedelta(days=20))
        await _audit(s, "transfer_initiative", iid, {"total": 200}, {"total": 300}, now - timedelta(days=1))
        await s.commit()
        for days, want in ((60, 100), (30, 150), (10, 200), (0, 300)):
            got = await initiative_budgets_as_of(s, 1, now - timedelta(days=days, seconds=-1))
            assert got[iid] == want, (days, got[iid])


async def _owned_initiative_with_key(provisioned, name, token_hash):
    from app.models import Initiative

    async with provisioned() as s:
        ini = Initiative(
            name=name, owner_upn=YAEL, group_id=1, department_id=1, budget=10, created_by=YAEL
        )
        s.add(ini)
        await s.flush()
        s.add(
            ServiceKey(
                owner_kind="initiative", owner_id=ini.id, max_budget=1, status="active",
                litellm_token_hash=token_hash,
            )
        )  # fmt: skip
        await s.commit()
        return ini.id


def _err(status, type_, message, key_hash):
    from datetime import UTC, datetime

    return dict(team_id="A1", user_id="svc", model="m", status_code=status, exception_type=type_,
                message=message, key_hash=key_hash, at=datetime.now(UTC).isoformat())  # fmt: skip


# review-p5-r12 major F1 (SystemDetail): the owner sees classified failures of their own subject
async def test_r13_subject_telemetry_is_scoped_to_the_subjects_keys(
    login_wm, provisioned, fake_app
):
    import httpx

    mine = await _owned_initiative_with_key(provisioned, "Mine", "hash-mine")
    await _owned_initiative_with_key(provisioned, "Other", "hash-other")
    rows = [
        _err(429, "max_parallel_requests", "Max parallel request limit", "hash-mine"),
        _err(500, "boom", "upstream", "hash-other"),
    ]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as c:
        assert (await c.post("/_test/errors", json=rows)).status_code == 200
    url = f"/service-keys/telemetry?owner_kind=initiative&owner_id={mine}"
    r = await (await login_wm("yael")).get(url)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert {x["reason"]: x["count"] for x in body["by_reason"]} == {"RATE_LIMIT": 1}
    assert [b["reason"] for b in body["last_blocks"]] == ["RATE_LIMIT"]
    assert (await (await login_wm("dani")).get(url)).status_code in (403, 404)  # scope isolation


# review-p5-r12 major F1 (SystemDetail): unreadable telemetry is reported, never shown as healthy
async def test_r13_subject_telemetry_unavailable_without_reader(login, provisioned):
    mine = await _owned_initiative_with_key(provisioned, "Mine", "hash-mine")
    client = await login("yael", litellm_database_url=None)
    r = await client.get(f"/service-keys/telemetry?owner_kind=initiative&owner_id={mine}")
    assert r.status_code == 200, r.text
    assert r.json()["available"] is False and r.json()["by_reason"] == []
