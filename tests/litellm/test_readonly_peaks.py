from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert

from app.litellm_client import LiteLLMReader
from fake_litellm.db import spend_logs

START = datetime(2026, 6, 1, tzinfo=UTC)
END = START + timedelta(days=2)


async def logs(engine, rows):
    async with engine.begin() as conn:
        await conn.execute(
            insert(spend_logs),
            [
                dict(request_id=str(i), user=u, team_id=t, startTime=s, endTime=e, spend=0)
                for i, (u, t, s, e) in enumerate(rows)
            ],
        )


async def test_overlaps_counted_half_open(fake_app, fake_engine, ro_engine):
    h = lambda n: START + timedelta(hours=n)  # noqa: E731
    await logs(
        fake_engine,
        [
            ("u", "t", h(1), h(2)),
            ("u", "t", h(2), h(3)),
            ("u", "t", h(2), h(4)),
            ("u", "t", h(2), h(2)),
        ],
    )
    rows = await LiteLLMReader(ro_engine).concurrency_peaks(START, END)
    assert [(r.user, r.day, r.peak, r.at) for r in rows] == [
        (None, START.date(), 2, h(2)),
        ("u", START.date(), 2, h(2)),
    ]


async def test_peak_per_user_and_global(fake_app, fake_engine, ro_engine):
    h = lambda n: START + timedelta(hours=n)  # noqa: E731
    await logs(
        fake_engine,
        [("u", "t", h(23), h(26)), ("v", "other", h(23), h(25)), ("u", "t", h(24), h(25))],
    )
    reader = LiteLLMReader(ro_engine)
    rows = await reader.concurrency_peaks(START, END)
    assert {(r.user, r.day): r.peak for r in rows} == {
        (None, START.date()): 2,
        ("u", START.date()): 1,
        ("v", START.date()): 1,
        (None, h(24).date()): 3,
        ("u", h(24).date()): 2,
        ("v", h(24).date()): 1,
    }
    filtered = await reader.concurrency_peaks(START, END, team_ids=["t"], users=["u"])
    assert max(r.peak for r in filtered if r.user is None) == 2
    assert await reader.concurrency_peaks(START, END, team_ids=[]) == []


async def test_rows_outside_window_ignored(fake_app, fake_engine, ro_engine):
    await logs(
        fake_engine,
        [
            ("u", "t", START - timedelta(days=1), START),
            ("u", "t", END, END + timedelta(hours=1)),
            ("v", "t", START - timedelta(hours=1), END + timedelta(hours=1)),
        ],
    )
    rows = await LiteLLMReader(ro_engine).concurrency_peaks(START, END)
    assert len(rows) == 4
    assert all(r.user in (None, "v") and r.peak == 1 for r in rows)
    assert {r.at for r in rows} == {START, START + timedelta(days=1)}


async def test_local_day_dst_boundaries_one_statement(fake_app, fake_engine, ro_engine):
    from zoneinfo import ZoneInfo

    from sqlalchemy import event

    tz = ZoneInfo("Asia/Jerusalem")
    start = datetime(2026, 10, 1, tzinfo=tz).astimezone(UTC)
    end = datetime(2026, 11, 1, tzinfo=tz).astimezone(UTC)
    await logs(fake_engine, [("u", "t", start - timedelta(hours=1), end + timedelta(hours=1))])
    statements = []

    def counted(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(ro_engine.sync_engine, "before_cursor_execute", counted)
    try:
        rows = await LiteLLMReader(ro_engine).concurrency_peaks(start, end, timezone=str(tz))
    finally:
        event.remove(ro_engine.sync_engine, "before_cursor_execute", counted)
    assert len(statements) == 1 and len(rows) == 62
    assert all(r.peak == 1 and r.at.astimezone(tz).hour == 0 for r in rows)
    with pytest.raises(ValueError):
        await LiteLLMReader(ro_engine).concurrency_peaks(
            start, end + timedelta(days=1), timezone=str(tz)
        )
