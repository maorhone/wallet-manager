import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.clock import FakeClock
from app.db import LockKind
from app.scheduler.loop import Job, run_loop


def clock():
    return FakeClock(datetime(2026, 10, 1, tzinfo=UTC))


async def _run(jobs, sf, c, stop):
    await asyncio.wait_for(run_loop(jobs, sf, c, stop=stop), 10)


async def test_jobs_run_at_their_intervals_with_fake_clock(seeded):
    c, stop, calls = clock(), asyncio.Event(), {"fast": [], "slow": []}

    def make(name):
        async def run():
            calls[name].append(c.now())
            if len(calls["fast"]) == 4:
                stop.set()

        return run

    jobs = [
        Job("fast", 11, timedelta(seconds=60), make("fast"), True),
        Job("slow", 12, timedelta(seconds=150), make("slow"), False),
    ]
    start = c.now()
    await _run(jobs, seeded, c, stop)
    fast = [(t - start).total_seconds() for t in calls["fast"]]
    slow = [(t - start).total_seconds() for t in calls["slow"]]
    assert fast[0] == 0 and all(60 <= b - a < 62 for a, b in zip(fast, fast[1:], strict=False))
    assert len(slow) == 1 and 150 <= slow[0] < 152  # first run after one interval


async def test_job_exception_logged_loop_continues(seeded, caplog):
    c, stop, calls = clock(), asyncio.Event(), []

    async def run():
        calls.append(c.now())
        if len(calls) == 1:
            raise RuntimeError("boom")
        stop.set()

    await _run([Job("flaky", 13, timedelta(seconds=5), run, True)], seeded, c, stop)
    assert len(calls) == 2
    assert "job flaky failed" in caplog.text and "boom" in caplog.text


async def test_second_replica_skips_while_job_lock_held(seeded):
    c, stop, ran = clock(), asyncio.Event(), []
    engine = seeded.kw["bind"]

    async def held():
        ran.append("held")

    async def other():
        ran.append("other")
        stop.set()

    jobs = [
        Job("held", 14, timedelta(seconds=5), held, True),
        Job("other", 15, timedelta(seconds=5), other, True),
    ]
    async with engine.connect() as conn:  # another replica holds the lock of "held"
        args = {"k": int(LockKind.JOB), "i": 14}
        assert await conn.scalar(text("SELECT pg_try_advisory_lock(:k, :i)"), args)
        await _run(jobs, seeded, c, stop)
        assert ran == ["other"]
        await conn.scalar(text("SELECT pg_advisory_unlock(:k, :i)"), args)
    stop.clear()
    ran.clear()
    await _run(jobs, seeded, c, stop)
    assert ran == ["held", "other"]  # the lock was released by the loop after each run


async def test_stop_event_ends_loop(seeded):
    c, stop = clock(), asyncio.Event()
    stop.set()
    await _run(
        [Job("never", 16, timedelta(seconds=1), lambda: asyncio.sleep(0), True)], seeded, c, stop
    )

    stop2, runs = asyncio.Event(), []

    async def run():
        runs.append(1)

    task = asyncio.create_task(
        run_loop([Job("tick", 17, timedelta(seconds=1), run, True)], seeded, c, stop=stop2)
    )
    while not runs:
        await asyncio.sleep(0)
    stop2.set()
    await asyncio.wait_for(task, 5)
