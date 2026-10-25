from urllib.parse import quote

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Department, LitellmWrite, Organization
from app.writes import WriteContext


async def test_missing_reason_422_invalid_request(login, put_budget):
    client = await login("admin")
    for reason in (None, "", "   "):
        r = await put_budget(client, reason=reason)
        assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"


@pytest.mark.usefixtures("wide_envelope")
async def test_same_idempotency_key_replays_without_duplicates(login, put_budget, seeded):
    client = await login("admin")
    first = await put_budget(client, budget=1100000, **{"Idempotency-Key": "k1"})
    second = await put_budget(client, budget=1100000, **{"Idempotency-Key": "k1"})
    assert first.status_code == 200 and second.status_code == 200
    rid = first.json()["request_id"]
    assert second.json()["request_id"] == rid and second.headers["x-request-id"] == rid
    assert second.json()["entities"][0]["values"]["budget"] == 1100000
    async with seeded() as s:
        audits = (await s.scalar(select(func.count()).select_from(AuditLog))) or 0
        writes = (await s.scalar(select(func.count()).select_from(LitellmWrite))) or 0
    assert (audits, writes) == (1, 1)

    other = await put_budget(client, budget=1100000, **{"Idempotency-Key": "k2"})
    assert other.json()["request_id"] != rid


@pytest.mark.usefixtures("wide_envelope")
async def test_audit_row_has_who_role_before_after_reason_request_id(login, put_budget, seeded):
    client = await login("admin")
    r = await put_budget(client, budget=1300000, reason=quote("תקציב חדש"))  # percent-encoded UTF-8
    assert r.status_code == 200
    body = r.json()
    async with seeded() as s:
        row = (await s.execute(select(AuditLog))).scalar_one()
        wr = (await s.execute(select(LitellmWrite))).scalar_one()
    assert row.actor_upn == "admin@corp.example" and row.actor_role == "platform_admin"
    assert (row.entity, row.entity_id) == ("department", "1")
    assert row.before["budget"] == 1000000 and row.after["budget"] == 1300000
    assert row.after["department_id"] == 1
    assert row.reason == "תקציב חדש"
    assert row.request_id == body["request_id"]
    assert wr.idempotency_key == f"{body['request_id']}:1"


async def test_domain_audit_outbox_single_transaction(login, put_budget, seeded, monkeypatch):
    async def boom(self, *a, **kw):
        raise RuntimeError("injected after audit")

    monkeypatch.setattr(WriteContext, "enqueue", boom)
    client = await login("admin")
    async with seeded() as s:  # the change must pass the envelope to reach the enqueue
        (await s.get(Organization, 1)).departments_envelope = 10_000_000
        await s.commit()
    with pytest.raises(RuntimeError):
        await put_budget(client, budget=1000001)
    async with seeded() as s:
        assert await s.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert await s.scalar(select(func.count()).select_from(LitellmWrite)) == 0
        assert (await s.get(Department, 1)).budget == 1000000
