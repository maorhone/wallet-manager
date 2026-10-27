"""Phase 5 round-14 re-review (fixed in round 15): regression tests."""

from datetime import UTC, datetime
from decimal import Decimal

from app.models import AuditLog
from app.services.history import values_as_of
from tests.api.test_review_money import DANI
from tests.api.test_transfers import move


# review-p5-r14 major B1a1: a retry without an Idempotency-Key must not move credit twice
async def test_r15_employee_transfer_requires_idempotency_key(login_wm):
    head = await login_wm("dana")
    headers = {"X-CSRF-Token": head.cookies["wm_csrf"], "X-Change-Reason": "transfer"}
    r = await head.post("/transfers", json=move(10, DANI), headers=headers)
    assert r.status_code == 422, r.text


def _audit(entity, entity_id, before, after, minute):
    return AuditLog(
        actor_upn="x", actor_role="system", entity=entity, entity_id=entity_id, before=before,
        after=after, reason="t", request_id=f"r{minute}-{entity_id}",
        at=datetime(2026, 6, 1, 0, minute, tzinfo=UTC),
    )  # fmt: skip


# review-p5-r14 major B3: a compensated transfer restores the initiative's budget in history
async def test_r15_history_includes_transfer_compensation(provisioned):
    async with provisioned() as s:
        s.add_all(
            [
                _audit("transfer_initiative", "7", {"total": 100}, {"total": 150}, 1),
                _audit(
                    "transfer_compensation", "3", {"initiative:7": 150}, {"initiative:7": 100}, 2
                ),
            ]
        )
        await s.commit()
        live = {"7": {"total": Decimal(100)}}
        at = datetime(2026, 6, 1, 0, 30, tzinfo=UTC)
        out = await values_as_of(
            s, "transfer_initiative", live, at, compensated=("initiative", "total")
        )
        assert out["7"]["total"] == Decimal(100)
