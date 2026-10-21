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


async def test_service_key_spend_by_hash_period_day_model(seeded):
    from app.litellm_client.readonly import SpendRow
    from app.models import ServiceKey

    async with seeded() as db:
        key = ServiceKey(
            owner_kind="group", owner_id=1, litellm_token_hash="dummy-hash", max_budget=10
        )
        db.add(key)
        await db.commit()
        key_id = str(key.id)

    class Reader:
        async def spend_rows(self, start, end):
            if not start <= NOW < end:
                return []
            return [
                SpendRow("A1", None, "dummy-hash", "gpt-4", NOW.date(), Decimal("1.25")),
                SpendRow("A1", None, "other-hash", "gpt-4", NOW.date(), Decimal("2")),
            ]

    await take_snapshot(seeded, Reader(), NOW)
    snap = await _snap(seeded)
    for period in ("2026-06", "2026-06-15"):
        for model in (None, "gpt-4"):
            assert snap[("service_key", key_id, period, model)] == Decimal("1.25")
    assert snap[("group", "1", "2026-06", None)] == Decimal("3.25")


async def test_service_key_spend_survives_rotation(seeded):
    from app.litellm_client.readonly import SpendRow
    from app.models import LitellmWrite, ServiceKey

    async with seeded() as db:
        key = ServiceKey(
            owner_kind="group", owner_id=1, litellm_token_hash="new-hash", max_budget=10
        )
        db.add(key)
        await db.flush()
        key_id = str(key.id)
        db.add(
            LitellmWrite(
                entity="service_key",
                entity_id=key_id,
                endpoint="/key/delete",
                payload={"keys": ["old-hash"]},
                idempotency_key="rotation-delete",
            )
        )
        await db.commit()

    class Reader:
        async def spend_rows(self, start, end):
            if not start <= NOW < end:
                return []
            return [
                SpendRow("A1", None, token, "gpt-4", NOW.date(), Decimal("1.25"))
                for token in ("old-hash", "new-hash")
            ]

    await take_snapshot(seeded, Reader(), NOW)
    assert (await _snap(seeded))[("service_key", key_id, "2026-06", None)] == Decimal("2.50")
