"""redesign U1: ONE commitment check (docs/design/commitments.md), regression tests."""

from datetime import UTC, datetime

from sqlalchemy import select, text

from app.models import Employee, ServiceKey
from tests.api.test_nested_group_budget import child_of
from tests.api.test_requests import submit
from tests.api.test_review_money import DANI
from tests.api.test_review_r7 import _log_at
from tests.helpers.commit import caps
from tests.helpers.commit import consume as _consume
from tests.api.test_service_keys import body as key_body
from tests.api.test_service_keys import post as post_key
from tests.api.test_transfers import send
from tests.helpers.grants import new_grant, new_share

A1 = 1  # base 250000, factor 1; three members at 15000 (45000 of caps)
GONE = "gone@corp.example"  # a departed member: the spend is in the group, in no allocation


async def _patch_parent(client, group_id, parent_id):
    h = {"X-CSRF-Token": client.cookies["wm_csrf"], "X-Change-Reason": "move"}
    return await client.patch(
        f"/groups/{group_id}/parent", json={"parent_group_id": parent_id}, headers=h
    )


def _put_caps(client, put, **by_upn):
    return put(client, f"/groups/{A1}/members/caps", caps(**by_upn))


# redesign U1 r18 B1b: child funding (validation.py:174) ignores consumption
async def test_u1_child_funding_counts_consumption(login, seeded, split, fake_engine, fake_app):
    child = await child_of(seeded, A1, 0)
    await _consume(fake_engine, user=GONE)  # 200000 consumed + 45000 of caps = 245000
    rep = await login("rep")
    bad = await split(rep, [{"group_id": child, "base": 100000}])
    assert bad.status_code == 422 and bad.json()["code"] == "PARENT_OVERALLOCATED", bad.text
    ok = await split(rep, [{"group_id": child, "base": 5000}])
    assert ok.status_code == 200, ok.text


# redesign U1 r18 B1b: the same child raise is refused (503) when spend cannot be read
async def test_u1_child_funding_refused_without_reader(login, seeded, split):
    child = await child_of(seeded, A1, 0)
    rep = await login("rep", litellm_database_url=None)
    r = await split(rep, [{"group_id": child, "base": 10}])
    assert r.status_code == 503 and r.json()["code"] == "SPEND_UNAVAILABLE", r.text


# redesign U1 r18 B3b: platform.move_group (platform.py:340) ignores consumption
async def test_u1_move_group_counts_consumption(login, seeded, fake_engine, fake_app):
    child = await child_of(seeded, None, 100000)
    await _consume(fake_engine, user=GONE)
    admin = await login("admin")
    r = await _patch_parent(admin, child, A1)
    assert r.status_code == 422 and r.json()["code"] == "PARENT_OVERALLOCATED", r.text


async def test_u1_move_group_fits_when_room(login, seeded, fake_engine, fake_app):
    child = await child_of(seeded, None, 3000)
    await _consume(fake_engine, 100000, user=GONE)
    r = await _patch_parent(await login("admin"), child, A1)
    assert r.status_code == 200, r.text


async def test_u1_move_group_refused_without_reader(login, seeded):
    child = await child_of(seeded, None, 3000)
    r = await _patch_parent(await login("admin", litellm_database_url=None), child, A1)
    assert r.status_code == 503 and r.json()["code"] == "SPEND_UNAVAILABLE", r.text


# redesign U1 r18 B3a: group-key rotation (service_keys.py:241) is not reserved
async def test_u1_group_key_rotation_keeps_old_generation_reserved(login_wm, provisioned):
    client = await login_wm("dana")
    first = await post_key(client, payload=key_body(max_budget=100000))
    assert first.status_code == 200, first.text
    key_id = first.json()["entities"][0]["id"]
    rotated = await post_key(client, f"/service-keys/{key_id}/rotate", {})
    assert rotated.status_code == 200, rotated.text  # 45000 + 100000 + 100000 fits
    more = await post_key(client, payload=key_body(max_budget=10000))
    assert more.status_code == 422 and more.json()["code"] == "PARENT_OVERALLOCATED", more.text


async def test_u1_group_key_rotation_refused_when_it_does_not_fit(login_wm, provisioned):
    client = await login_wm("dana")
    first = await post_key(client, payload=key_body(max_budget=130000))
    key_id = first.json()["entities"][0]["id"]  # 45000 + 130000; a second generation: 305000
    rotated = await post_key(client, f"/service-keys/{key_id}/rotate", {})
    assert rotated.status_code == 422 and rotated.json()["code"] == "PARENT_OVERALLOCATED"
    async with provisioned() as s:
        key = await s.get(ServiceKey, int(key_id))
        assert key.retired_budget == 0 and key.status == "active"


# redesign U1 r18 B1c: PMO-funded consumption (requests.py:368) is not charged as base
async def test_u1_pmo_funded_consumption_is_not_base_personal(login_wm, fake_engine):
    grant = await new_grant(login_wm, amount=170000)
    await new_share(login_wm, grant, DANI, 170000)
    await _log_at(fake_engine, 1, "A1", DANI, 185000, datetime.now(UTC))  # 15000 base + 170000 PMO
    dani, dana = await login_wm("dani"), await login_wm("dana")
    rid = await submit(dani, amount=100)
    r = await send(
        dana, "POST", f"/requests/{rid}/decide", {"decision": "approve", "amount": 40000}
    )
    assert r.status_code == 200, r.text  # was a false PARENT_OVERALLOCATED (185000 + 30000 + 40000)


async def test_u1_pmo_funded_consumption_is_not_base_initiative(login_wm, fake_engine):
    dana, dani = await login_wm("dana"), await login_wm("dani")
    made = await send(
        dana, "POST", f"/groups/{A1}/initiatives", {"name": "P", "owner_upn": DANI, "budget": 1000}
    )
    iid = int(made.json()["entities"][0]["id"])
    grant = await new_grant(login_wm, amount=170000)
    await new_share(login_wm, grant, DANI, 170000)
    await _log_at(fake_engine, 1, "A1", DANI, 185000, datetime.now(UTC))
    rid = await submit(dani, amount=100, target={"kind": "initiative", "id": iid})
    r = await send(
        dana, "POST", f"/requests/{rid}/decide", {"decision": "approve", "amount": 40000}
    )
    assert r.status_code == 200, r.text


# r17 B1b/B1c/B3 re-check: revoked-key and departed-member consumption stay committed
async def test_u1_revoked_key_consumption_counts(login_wm, provisioned, fake_engine):
    client = await login_wm("dana")
    first = await post_key(client, payload=key_body(max_budget=60000))
    key_id = int(first.json()["entities"][0]["id"])
    async with provisioned() as s:
        token = (await s.get(ServiceKey, key_id)).litellm_token_hash
    async with fake_engine.begin() as conn:
        await conn.execute(
            text(
                'INSERT INTO "LiteLLM_SpendLogs" (request_id, "user", team_id, model_group,'
                ' api_key, "startTime", "endTime", spend)'
                " VALUES ('u1-k', NULL, 'A1', 'gpt-4', :k, now(), now(), 100000)"
            ),
            {"k": token},
        )
    assert (await post_key(client, f"/service-keys/{key_id}/revoke", {})).status_code == 200
    r = await post_key(client, payload=key_body(max_budget=120000))  # 100000 + 45000 + 120000
    assert r.status_code == 422 and r.json()["code"] == "PARENT_OVERALLOCATED", r.text
    ok = await post_key(client, payload=key_body(max_budget=100000))
    assert ok.status_code == 200, ok.text


async def test_u1_departed_member_consumption_counts_for_approvals(login_wm, fake_engine):
    await _consume(fake_engine, user=GONE)
    dani, dana = await login_wm("dani"), await login_wm("dana")
    rid = await submit(dani, amount=100)
    r = await send(
        dana, "POST", f"/requests/{rid}/decide", {"decision": "approve", "amount": 40000}
    )
    assert r.status_code == 422 and r.json()["code"] == "PARENT_OVERALLOCATED", r.text


# r18 decision 2: a no-op cap save is not checked; an increase with unreadable spend is a 503
async def test_u1_noop_cap_save_skips_the_check(login, seeded, put, fake_engine, fake_app):
    await _consume(fake_engine, 400000, user=GONE)  # the group is already over-allocated
    head = await login("dana")
    same = await _put_caps(head, put, dani=15000, yael=15000)
    assert same.status_code == 200, same.text
    bad = await _put_caps(head, put, yael=16000)
    assert bad.status_code == 422 and bad.json()["code"] == "PARENT_OVERALLOCATED", bad.text


async def test_u1_cap_increase_without_reader_is_503(login, put):
    head = await login("dana", litellm_database_url=None)
    r = await _put_caps(head, put, yael=16000)
    assert r.status_code == 503 and r.json()["code"] == "SPEND_UNAVAILABLE", r.text
    same = await _put_caps(head, put, yael=15000)
    assert same.status_code == 200, same.text


# neighbouring floors: a cut may not go below consumption
async def test_u1_cap_cut_floor_still_applies(login, put, fake_engine, fake_app):
    await _log_at(fake_engine, 1, "A1", DANI, 12000, datetime.now(UTC))
    head = await login("dana")
    r = await _put_caps(head, put, dani=10000)
    assert r.status_code == 422 and r.json()["details"]["reason"] == "below_consumed", r.text
    assert (await _put_caps(head, put, dani=13000)).status_code == 200


async def test_u1_member_caps_in_db_are_untouched_when_refused(
    login, put, seeded, fake_engine, fake_app
):
    await _consume(fake_engine, user=GONE)
    head = await login("dana")
    assert (await _put_caps(head, put, yael=40000)).status_code == 422
    async with seeded() as s:
        caps_now = list(await s.scalars(select(Employee.base_cap).where(Employee.group_id == A1)))
    assert sorted(caps_now) == [15000, 15000, 15000]
