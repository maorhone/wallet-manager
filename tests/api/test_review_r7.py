"""Phase 5 round-6 re-review (fixed in round 7): regression tests."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.models import Department
from tests.api.test_transfers import move, send

DANI = "dani@corp.example"


async def _log_at(fake_engine, i, team, user, spend, at):
    async with fake_engine.begin() as conn:
        await conn.execute(
            text(
                'INSERT INTO "LiteLLM_SpendLogs" (request_id, "user", team_id, model_group,'
                ' api_key, "startTime", "endTime", spend)'
                " VALUES (:i, :u, :t, 'gpt-4', NULL, :at, :at, :s)"
            ),
            {"i": f"r7-{i}", "u": user, "t": team, "at": at, "s": spend},
        )


# review-p5-r6 blocker B1a2: multi-month budgets include last month's consumption
async def test_r7_transfer_reads_active_multi_month_window(login_wm, provisioned, fake_engine):
    last_month = datetime.now(UTC).replace(day=1, hour=12) - timedelta(days=3)
    await _log_at(fake_engine, 1, "A1", DANI, 14500, last_month)
    async with provisioned() as s:
        (await s.get(Department, 1)).budget_period = "2mo"
        await s.commit()
    head = await login_wm("dana")
    r = await send(head, "POST", "/transfers", move(1000))
    assert r.status_code == 422, r.text
    assert r.json()["details"]["reason"] == "exceeds_unused"
