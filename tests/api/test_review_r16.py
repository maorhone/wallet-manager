"""Phase 5 round-15 re-review (fixed in round 16): regression tests."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import AuditLog, Transfer
from app.outbox.worker import drain
from app.services.history import values_with_transfers
from tests.api.test_review_money import DANI, YAEL
from tests.api.test_transfers import move, send


# review-p5-r15 blocker B1a1: without a spend reader nothing may release transfer credit
async def test_r16_transfer_stays_pending_without_reader(
    login_wm, provisioned, litellm, prov_settings
):
    head = await login_wm("dana")
    assert (await send(head, "POST", "/transfers", move(10, DANI, YAEL))).status_code == 200
    await drain(provisioned, litellm, prov_settings)
    await drain(provisioned, litellm, prov_settings)
    async with provisioned() as s:
        assert set(await s.scalars(select(Transfer.state))) == {"pending"}


def _audit(entity, entity_id, before, after, minute):
    return AuditLog(
        actor_upn="x", actor_role="system", entity=entity, entity_id=entity_id, before=before,
        after=after, reason="t", request_id=f"r{minute}-{entity}-{entity_id}",
        at=datetime(2026, 6, 1, 0, minute, tzinfo=UTC),
    )  # fmt: skip


# review-p5-r15 major B3: historical system/employee/group totals include transfers
async def test_r16_history_includes_transfers(provisioned):
    async with provisioned() as s:
        s.add_all(
            [
                _audit("production_system", "5", {"budget": 0}, {"budget": 100}, 1),
                _audit("transfer_system", "5", {"total": 100}, {"total": 150}, 10),
                _audit("transfer_employee", "a@corp.example", {"total": 5}, {"total": 9}, 10),
                _audit("transfer_group", "3", {"total": 7}, {"total": 8}, 10),
            ]
        )
        await s.commit()
        before = datetime(2026, 6, 1, 0, 5, tzinfo=UTC)
        after = datetime(2026, 6, 1, 0, 30, tzinfo=UTC)
        cases = [
            ("system", "5", "budget", 100, 150),
            ("employee", "a@corp.example", "base_cap", 5, 9),
            ("group", "3", "base_budget", 7, 8),
        ]
        for kind, id_, field, was, now in cases:
            live = {id_: {field: Decimal(now)}}
            assert (await values_with_transfers(s, kind, live, before))[id_][field] == was
            assert (await values_with_transfers(s, kind, live, after))[id_][field] == now


# review-p5-r16 blocker B1b: consumed credit (e.g. a revoked key) stays committed for new funding
async def test_r17_initiative_refused_after_group_consumption(login_wm, fake_engine):
    from tests.api.test_review_r7 import _log_at

    await _log_at(fake_engine, 1, "A1", DANI, 10**8, datetime.now(UTC))
    head = await login_wm("dana")
    body = {"name": "Pilot", "owner_upn": DANI, "budget": 1000}
    r = await send(head, "POST", "/groups/1/initiatives", body)
    assert r.status_code == 422 and r.json()["code"] == "PARENT_OVERALLOCATED", r.text


# review-p5-r16 blocker B1a1: an uncertain (attempted, outcome unknown) destination write is
# republished with its restored value when the source credit is restored
async def test_r17_uncertain_destination_restored_on_late_spend(
    login_wm, provisioned, fake_engine, litellm, prov_settings, ro_engine
):
    from app.litellm_client import LiteLLMReader
    from app.litellm_client.errors import LiteLLMTransientError
    from app.models import Employee, LitellmWrite
    from tests.api.test_review_money import YAEL
    from tests.api.test_review_r7 import _log_at

    head = await login_wm("dana")
    async with provisioned() as s:
        base = (await s.get(Employee, YAEL)).base_cap
    assert (await send(head, "POST", "/transfers", move(50, DANI, YAEL))).status_code == 200
    real, sent = litellm.team_member_update, []

    async def lost_response(body):
        if body.user_id == YAEL:
            sent.append(body.max_budget_in_team)
            await real(body)  # the update lands upstream ...
            if len(sent) == 1:
                raise LiteLLMTransientError("response lost")  # ... but the answer is lost
            return None
        return await real(body)

    litellm.team_member_update = lost_response
    reader = LiteLLMReader(ro_engine)
    await drain(provisioned, litellm, prov_settings, reader)
    assert sent == [float(base + 50)]
    await _log_at(fake_engine, 1, "A1", DANI, 10**8, datetime.now(UTC))  # late source spend
    for _ in range(3):
        await drain(provisioned, litellm, prov_settings, reader)
    assert sent[-1] == float(base)  # the destination credit was reversed upstream
    async with provisioned() as s:
        assert (await s.scalar(select(Transfer.state))) == "compensated"
        keys = set(await s.scalars(select(LitellmWrite.idempotency_key)))
        assert any(":restore-target:" in k for k in keys)


# review-p5-r16 major B3 (overview:114): a descendant's PMO grant joins the rolled-up total once
async def test_r17_overview_total_includes_descendant_grants(login, provisioned):
    from app.models import Grant
    from tests.api.test_review_money import _child

    gid = await _child(provisioned)
    head = await login("dana")
    before = (await head.get("/budget/overview?scope=group:1")).json()["kpis"]["total"]
    async with provisioned() as s:
        s.add(Grant(department_id=1, group_id=gid, amount=100, status="active", created_by="x"))
        await s.commit()
    body = (await head.get("/budget/overview?scope=group:1")).json()
    assert body["kpis"]["total"] == before + 100
    assert body["kpis"]["remaining"] == body["kpis"]["total"] - body["kpis"]["used"]


# review-p5-r16 major B3 (rate_card:76): one currency per rate card, also across later edits
async def test_r17_rate_card_single_currency(login, put, seeded):
    admin = await login("admin")
    mixed = {
        "rates": [
            {"model": "gpt-4", "input_per_1m": "10", "output_per_1m": "30", "currency": "USD"},
            {"model": "claude", "input_per_1m": "3", "output_per_1m": "15", "currency": "EUR"},
        ]
    }
    assert (await put(admin, "/rate-card", mixed)).status_code == 422
    usd = {"rates": [{**mixed["rates"][0]}]}
    assert (await put(admin, "/rate-card", usd)).status_code == 200
    eur = {"rates": [{**mixed["rates"][1]}]}  # a later edit may not introduce another currency
    r = await put(admin, "/rate-card", eur)
    assert r.status_code == 422, r.text


# review-p5-r16 major F1 (Initiative.tsx:18): systems delegated to a section the caller heads
async def test_r17_head_sees_system_managed_by_section(login, provisioned):
    from app.models import ProductionSystem

    async with provisioned() as s:
        s.add(
            ProductionSystem(
                name="Billing", department_id=1, owner_upn="yael@corp.example", status="active",
                managing_group_id=1, budget=10, funding="department",
            )
        )  # fmt: skip
        await s.commit()
    mine = (await (await login("dana")).get("/systems/mine")).json()["items"]
    assert [x["name"] for x in mine] == ["Billing"]
    other = (await (await login("dani")).get("/systems/mine")).json()["items"]
    assert [x["name"] for x in other] == []
