from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text

from app.litellm_client import LiteLLMReader
from app.models import ProductionSystem, SpendSnapshot
from app.services.spend import take_snapshot

NOW = datetime(2026, 6, 15, 12, tzinfo=ZoneInfo("Asia/Jerusalem"))
DANI, YAEL = "dani@corp.example", "yael@corp.example"


async def _log(fake_engine, i, team, user, model, spend, at):
    async with fake_engine.begin() as conn:
        await conn.execute(
            text(
                'INSERT INTO "LiteLLM_SpendLogs" (request_id, "user", team_id, model_group,'
                ' "startTime", "endTime", spend) VALUES (:i, :u, :t, :m, :at, :at, :s)'
            ),
            {"i": f"r{i}", "u": user, "t": team, "m": model, "at": at, "s": spend},
        )


async def _snap(sf) -> dict:
    async with sf() as s:
        rows = (await s.scalars(select(SpendSnapshot))).all()
    return {(r.scope_kind, r.scope_id, r.period, r.model): r.spend for r in rows}


@pytest.fixture
async def spend(fake_app, fake_engine, seeded):
    at = lambda d: datetime(2026, 6, d, 9, tzinfo=UTC)  # noqa: E731
    await _log(fake_engine, 1, "A1", DANI, "gpt-4", 10, at(3))
    await _log(fake_engine, 2, "A1", DANI, "claude", 5, at(3))
    await _log(fake_engine, 3, "A1", YAEL, "gpt-4", 7, at(4))
    await _log(fake_engine, 4, "A2", DANI, "gpt-4", 100, at(4))  # dani's old group
    await _log(fake_engine, 5, "A1", DANI, "gpt-4", 2, datetime(2026, 5, 20, 9, tzinfo=UTC))
    return seeded


async def test_snapshot_groups_by_scope_and_model(spend, ro_engine):
    await take_snapshot(spend, LiteLLMReader(ro_engine), NOW)
    snap = await _snap(spend)
    d = Decimal
    assert snap[("group", "1", "2026-06", None)] == d(22)  # A1
    assert snap[("group", "1", "2026-06", "gpt-4")] == d(17)
    assert snap[("group", "1", "2026-06", "claude")] == d(5)
    assert snap[("group", "1", "2026-06-03", "gpt-4")] == d(10)
    assert snap[("group", "1", "2026-06-03", None)] == d(15)
    assert snap[("department", "1", "2026-06", None)] == d(122)  # A1 + A2
    assert snap[("employee", YAEL, "2026-06", None)] == d(7)
    assert snap[("employee", DANI, "2026-06-03", "claude")] == d(5)
    # the previous period is filled when missing
    assert snap[("group", "1", "2026-05", None)] == d(2)


async def test_employee_spend_only_current_group(spend, ro_engine):
    await take_snapshot(spend, LiteLLMReader(ro_engine), NOW)
    snap = await _snap(spend)
    assert snap[("employee", DANI, "2026-06", None)] == 15  # A2's 100 is not membership spend
    assert snap[("group", "2", "2026-06", None)] == 100  # but A2 still carries it


async def test_snapshot_replaces_period_rows(spend, ro_engine, fake_engine):
    reader = LiteLLMReader(ro_engine)
    await take_snapshot(spend, reader, NOW)
    await _log(fake_engine, 9, "A1", YAEL, "gpt-4", 3, datetime(2026, 6, 5, 9, tzinfo=UTC))
    await take_snapshot(spend, reader, NOW)
    snap = await _snap(spend)
    assert snap[("group", "1", "2026-06", None)] == 25
    assert len([k for k in snap if k[:4] == ("group", "1", "2026-06", None)]) == 1


async def test_ro_db_down_keeps_last_snapshot(spend, ro_engine):
    await take_snapshot(spend, LiteLLMReader(ro_engine), NOW)
    before = await _snap(spend)

    class Down:
        async def spend_rows(self, start, end):
            raise ConnectionError("litellm db unreachable")

    await take_snapshot(spend, Down(), NOW)
    assert await _snap(spend) == before


async def test_system_spend_snapshotted(seeded, fake_app, fake_engine, ro_engine):
    async with seeded() as s:
        s.add(
            ProductionSystem(department_id=1, name="Sys A", litellm_team_id="sys-1", budget=10000)
        )
        await s.commit()
    await _log(fake_engine, 20, "sys-1", None, "gpt-4", 12, datetime(2026, 6, 3, 9, tzinfo=UTC))
    await take_snapshot(seeded, LiteLLMReader(ro_engine), NOW)
    snap = await _snap(seeded)
    assert snap[("system", "1", "2026-06", None)] == 12
    assert snap[("system", "1", "2026-06", "gpt-4")] == 12
    assert ("group", "1", "2026-06", None) not in snap  # a system is not a department group
