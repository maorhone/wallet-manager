"""Phase 5 round-17 re-review (fixed in round 18): regression tests."""

import uuid
from datetime import UTC, datetime

from app.outbox.worker import drain
from tests.api.test_requests import send, submit
from tests.api.test_review_money import DANI
from tests.api.test_review_r7 import _log_at
from tests.api.test_service_keys import body as key_body
from tests.api.test_service_keys import post as post_key

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
