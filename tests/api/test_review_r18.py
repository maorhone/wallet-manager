"""Phase 5 round-17 re-review (fixed in round 18): regression tests."""

import uuid
from datetime import UTC, datetime

from app.outbox.worker import drain
from tests.api.test_requests import submit
from tests.api.test_review_money import DANI
from tests.api.test_review_r7 import _log_at
from tests.api.test_service_keys import body as key_body
from tests.api.test_service_keys import post as post_key
from tests.api.test_transfers import send

A1 = 1  # base 250000, factor 1; three members at 15000


async def _consume(fake_engine, amount=200000):
    # consumption that no current allocation shows (departed member / revoked key look the same)
    await _log_at(fake_engine, 1, "A1", DANI, amount, datetime.now(UTC))


def caps(**by_upn):
    return [{"upn": f"{u}@corp.example", "base_cap": c} for u, c in by_upn.items()]


# review-p5-r17 blocker B1b/B3: member cap increases count consumed credit as committed
async def test_r18_member_caps_count_consumption(login_wm, fake_engine):
    await _consume(fake_engine)
    head = await login_wm("dana")
    r = await head.put(
        f"/groups/{A1}/members/caps",
        json=caps(yael=40000),
        headers={"X-CSRF-Token": head.cookies["wm_csrf"], "X-Change-Reason": "t"},
    )
    assert r.status_code == 422 and r.json()["code"] == "PARENT_OVERALLOCATED", r.text


async def test_r18_member_caps_small_increase_still_works(login_wm, fake_engine):
    await _consume(fake_engine, 100000)
    head = await login_wm("dana")
    r = await head.put(
        f"/groups/{A1}/members/caps",
        json=caps(yael=20000),
        headers={"X-CSRF-Token": head.cookies["wm_csrf"], "X-Change-Reason": "t"},
    )
    assert r.status_code == 200, r.text


# review-p5-r17 blocker B1b: bulk commit goes through the same rule
async def test_r18_bulk_commit_counts_consumption(login_wm, fake_engine):
    await _consume(fake_engine)
    head = await login_wm("dana")
    h = {"X-CSRF-Token": head.cookies["wm_csrf"], "X-Change-Reason": "bulk"}
    body = {"upns": ["yael@corp.example"], "amount": 40000}
    r = await head.post(f"/groups/{A1}/bulk-allocation", json=body, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "PARENT_OVERALLOCATED", r.text


# review-p5-r17 blocker B1b/B3: group-key creation counts consumed credit
async def test_r18_group_key_counts_consumption(login_wm, fake_engine):
    await _consume(fake_engine)
    head = await login_wm("dana")
    r = await post_key(head, payload=key_body(max_budget=60000))
    assert r.status_code == 422 and r.json()["code"] == "PARENT_OVERALLOCATED", r.text


async def test_r18_group_key_small_still_works(
    login_wm, fake_engine, provisioned, litellm, prov_settings
):
    await drain(provisioned, litellm, prov_settings)
    await _consume(fake_engine, 100000)
    head = await login_wm("dana")
    r = await post_key(head, payload=key_body(max_budget=60000))
    assert r.status_code == 200, r.text


# review-p5-r17 blocker B1b: initiative funding refuses without a spend reader
async def test_r18_initiative_refused_without_reader(login):
    head = await login("dana", litellm_database_url=None)
    r = await head.post(
        f"/groups/{A1}/initiatives",
        json={"name": "Pilot", "owner_upn": DANI, "budget": 10},
        headers={
            "X-CSRF-Token": head.cookies["wm_csrf"],
            "X-Change-Reason": "t",
            "Idempotency-Key": uuid.uuid4().hex,
        },
    )
    assert r.status_code == 503 and r.json()["code"] == "SPEND_UNAVAILABLE", r.text


# review-p5-r17 blocker B1c: credit approvals count consumed credit (personal and targeted)
async def test_r18_personal_credit_approval_counts_consumption(login_wm, fake_engine):
    await _consume(fake_engine)
    dani, dana = await login_wm("dani"), await login_wm("dana")
    rid = await submit(dani, amount=100)
    r = await send(
        dana, "POST", f"/requests/{rid}/decide", {"decision": "approve", "amount": 40000}
    )
    assert r.status_code == 422 and r.json()["code"] == "PARENT_OVERALLOCATED", r.text


async def test_r18_initiative_credit_approval_counts_consumption(login_wm, fake_engine):
    dani, dana = await login_wm("dani"), await login_wm("dana")
    made = await send(
        dana, "POST", f"/groups/{A1}/initiatives", {"name": "P", "owner_upn": DANI, "budget": 1000}
    )
    assert made.status_code == 200, made.text
    iid = made.json()["entities"][0]["id"]
    await _consume(fake_engine)
    rid = await submit(dani, amount=100, target={"kind": "initiative", "id": int(iid)})
    r = await send(
        dana, "POST", f"/requests/{rid}/decide", {"decision": "approve", "amount": 40000}
    )
    assert r.status_code == 422 and r.json()["code"] == "PARENT_OVERALLOCATED", r.text


# review-p5-r17 blocker B1a1: the source credit is restored upstream only after every uncertain
# destination reduction is confirmed; a permanently failing repair keeps it held
async def test_r18_source_restore_waits_for_destination_repair(
    login_wm, provisioned, fake_engine, litellm, prov_settings, ro_engine
):
    from sqlalchemy import select

    from app.litellm_client import LiteLLMReader
    from app.litellm_client.errors import LiteLLMPermanentError, LiteLLMTransientError
    from app.models import Employee, LitellmWrite, Transfer
    from tests.api.test_review_money import YAEL
    from tests.api.test_transfers import move

    head = await login_wm("dana")
    assert (await send(head, "POST", "/transfers", move(50, DANI, YAEL))).status_code == 200
    real, calls = litellm.team_member_update, []

    async def flaky(body):
        if body.user_id == YAEL:
            calls.append(body.max_budget_in_team)
            if len(calls) == 1:
                await real(body)
                raise LiteLLMTransientError("response lost")
            raise LiteLLMPermanentError(400, "bad")
        return await real(body)

    litellm.team_member_update = flaky
    reader = LiteLLMReader(ro_engine)
    await drain(provisioned, litellm, prov_settings, reader)
    await _consume(fake_engine, 10**8)
    for _ in range(3):
        await drain(provisioned, litellm, prov_settings, reader)
    async with provisioned() as s:
        assert await s.scalar(select(Transfer.state)) == "compensated"
        status = dict(
            (await s.execute(select(LitellmWrite.idempotency_key, LitellmWrite.status))).all()
        )
        target = [v for k, v in status.items() if ":restore-target:" in k]
        assert target and target != ["applied"]
        assert [v for k, v in status.items() if k.endswith(":restore-source")] == ["pending"]
        assert (await s.get(Employee, DANI)) is not None


# review-p5-r17 major B2: the head of a delegated system's section reads its keys and telemetry
async def test_r18_head_reads_delegated_system_details(login, provisioned):
    from app.models import ProductionSystem

    async with provisioned() as s:
        system = ProductionSystem(
            name="Billing", department_id=1, owner_upn="yael@corp.example", status="active",
            managing_group_id=1, budget=10, funding="department",
        )  # fmt: skip
        s.add(system)
        await s.commit()
        sid = system.id
    q = f"owner_kind=system&owner_id={sid}"
    head, member = await login("dana"), await login("dani")
    for path in ("/service-keys", "/service-keys/telemetry"):
        assert (await head.get(f"{path}?{q}")).status_code == 200, path
        assert (await member.get(f"{path}?{q}")).status_code == 403, path
    listed = (await head.get(f"/service-keys?{q}")).json()
    assert listed["items"] == []
    # read only: the head cannot create keys on it
    r = await post_key(head, payload=key_body(owner_kind="system", owner_id=sid))
    assert r.status_code == 403, r.text


# review-p5-r17 major B3: rows fund the same subtree they consume (PMO grants once)
async def test_r18_overview_rows_include_pmo_funding(login, provisioned):
    from app.models import Grant
    from tests.api.test_review_money import _child

    gid = await _child(provisioned, base=100)
    async with provisioned() as s:
        s.add(Grant(department_id=1, group_id=gid, amount=100, status="active", created_by="x"))
        await s.commit()
    from app.models import SpendSnapshot

    async with provisioned() as s:
        s.add(
            SpendSnapshot(
                scope_kind="group", scope_id=str(gid), period=datetime.now(UTC).strftime("%Y-%m"),
                model=None, spend=150,
            )
        )  # fmt: skip
        await s.commit()
    head = await login("dana")
    rows = (await head.get("/budget/overview?scope=group:1")).json()["rows"]
    child = next(r for r in rows if r["kind"] == "group")
    assert (child["total"], child["used"], float(child["percent"])) == (200, 150, 0.75)
    attention = (await head.get("/attention?scope=group:1")).json()
    assert [r["id"] for r in attention["shortage_risks"] if r["kind"] == "group"] == []
    dept = (await (await login("admin")).get("/budget/overview?scope=department:1")).json()
    section = next(x for x in dept["sections"] if x["id"] == 1)
    assert section["total"] >= 200  # base plus the child's grant, once


# review-p5-r17 major B3: concurrent first writes cannot mix currencies on an empty card
async def test_r18_rate_card_first_writes_serialized(login, put, seeded, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.services import rate_card

    real = asyncio.sleep

    async def slow(_):
        await real(0.3)

    monkeypatch.setattr(rate_card, "asyncio", SimpleNamespace(sleep=slow))

    admin = await login("admin")

    def card(model, currency):
        return {
            "rates": [
                {"model": model, "input_per_1m": "1", "output_per_1m": "2", "currency": currency}
            ]
        }

    done = await asyncio.gather(
        put(admin, "/rate-card", card("gpt-4", "USD")),
        put(admin, "/rate-card", card("claude", "EUR")),
    )
    assert sorted(r.status_code for r in done) == [200, 422], [r.text for r in done]
    rates = (await admin.get("/rate-card")).json()
    assert len({x["currency"] for x in rates["rates"]}) == 1
