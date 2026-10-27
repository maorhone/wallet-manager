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
