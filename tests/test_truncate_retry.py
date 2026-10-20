"""truncate_with_retry: a TRUNCATE behind another session's lock waits briefly, then retries."""

import pytest
from helpers.db import drop_leaked_sessions
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import is_lock_conflict, truncate_with_retry


async def _hold_read_lock(engine):
    conn = await engine.connect()
    await conn.begin()
    await conn.execute(text('SELECT 1 FROM "policy" LIMIT 1'))  # ACCESS SHARE until rollback
    return conn


async def test_retries_after_lock_timeout_once_the_blocker_is_gone(engine):
    blocker = await _hold_read_lock(engine)
    calls = []

    async def release(_eng):
        calls.append(1)
        await blocker.rollback()

    try:
        await truncate_with_retry(
            engine, 'TRUNCATE "policy"', lock_timeout_ms=200, on_conflict=release
        )
    finally:
        await blocker.close()
    assert calls == [1]


async def test_gives_up_after_the_last_attempt(engine):
    blocker = await _hold_read_lock(engine)
    try:
        with pytest.raises(DBAPIError) as err:
            await truncate_with_retry(engine, 'TRUNCATE "policy"', attempts=2, lock_timeout_ms=100)
        assert is_lock_conflict(err.value)
    finally:
        await blocker.close()


async def test_other_errors_are_not_retried(engine):
    calls = []

    async def spy(_eng):
        calls.append(1)

    with pytest.raises(DBAPIError) as err:
        await truncate_with_retry(engine, 'TRUNCATE "no_such_table"', on_conflict=spy)
    assert not is_lock_conflict(err.value) and calls == []


async def test_leaked_session_is_dropped_so_the_clean_up_does_not_hang(engine):
    leaked = await _hold_read_lock(engine)
    with pytest.warns(UserWarning, match="terminated leaked DB session"):
        await truncate_with_retry(
            engine, 'TRUNCATE "policy"', lock_timeout_ms=200, on_conflict=drop_leaked_sessions
        )
    await leaked.invalidate()
    await leaked.close()
