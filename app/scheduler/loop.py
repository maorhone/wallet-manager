"""Scheduler loop (SPEC §5.1): ``python -m app.scheduler``.

Jobs are due by an in-memory timetable; all business state lives in the database, so a restart
catches up on whatever is overdue. A job runs only while it holds ``pg_try_advisory_lock(5, id)``,
so with several replicas at most one runs it at a time.
"""

import asyncio
import logging
import signal
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import metrics
from app.clock import Clock, SystemClock
from app.config import Settings
from app.db import LockKind, make_engine, make_session_factory
from app.directory import FixtureDirectory
from app.litellm_client import LiteLLMClient, LiteLLMReader
from app.scheduler import jobs

log = logging.getLogger("app.scheduler")

GRANTS_LOCK, NOTIFY_LOCK, RECONCILE_LOCK, REPORT_ALERTS_LOCK = 1, 2, 3, 4


@dataclass(frozen=True)
class Job:
    name: str
    lock_id: int
    interval: timedelta
    run: Callable[[], Awaitable[object]]
    run_at_start: bool
    timeout_s: float = 300


async def _run_one(job: Job, engine) -> None:
    """Run ``job`` under its advisory lock on a dedicated connection."""
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        args = {"k": int(LockKind.JOB), "i": job.lock_id}
        if not await conn.scalar(text("SELECT pg_try_advisory_lock(:k, :i)"), args):
            log.info("job %s is held by another replica, skipped", job.name)
            metrics.SCHEDULER_RUNS.labels(job.name, "skipped").inc()
            return
        try:
            await asyncio.wait_for(job.run(), job.timeout_s)
        except TimeoutError:
            log.error("job %s timed out after %ss", job.name, job.timeout_s)
            metrics.SCHEDULER_RUNS.labels(job.name, "timeout").inc()
        except Exception:
            log.exception("job %s failed", job.name)
            metrics.SCHEDULER_RUNS.labels(job.name, "error").inc()
        else:
            metrics.SCHEDULER_RUNS.labels(job.name, "ok").inc()
            metrics.SCHEDULER_LAST_SUCCESS.labels(job.name).set(time.time())
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:k, :i)"), args)


async def run_loop(
    jobs_: Sequence[Job], session_factory, clock: Clock, *, stop: asyncio.Event
) -> None:
    engine = session_factory.kw["bind"]
    start = clock.now()
    due = {j.name: start if j.run_at_start else start + j.interval for j in jobs_}
    while not stop.is_set():
        now = clock.now()
        for job in jobs_:
            if stop.is_set():
                break
            if due[job.name] <= now:
                due[job.name] = now + job.interval
                await _run_one(job, engine)
        if stop.is_set():
            break
        wait = (min(due.values()) - clock.now()).total_seconds()
        await clock.sleep(max(0.0, min(wait, 1.0)))


def build_jobs(settings: Settings, session_factory, client, reader, directory, clock) -> list[Job]:
    """Independent jobs, with reconciliation delayed by its first interval."""
    timeout = settings.scheduler_lock_timeout_s
    return [
        Job("reconcile", RECONCILE_LOCK, timedelta(minutes=settings.reconcile_interval_min),
            lambda: jobs.run_reconcile(session_factory, client, clock, settings=settings),
            False, timeout),
        Job(
            "grants", GRANTS_LOCK, timedelta(seconds=settings.scheduler_grants_interval_s),
            lambda: jobs.run_grants(
                session_factory, client, clock, reader=reader, settings=settings
            ),
            True, timeout,
        ),
        Job(
            "notifications", NOTIFY_LOCK, timedelta(seconds=settings.scheduler_notify_interval_s),
            lambda: jobs.run_notifications(
                session_factory, client, clock, settings=settings, directory=directory
            ),
            True, timeout,
        ),
        Job(
            "report_alerts", REPORT_ALERTS_LOCK,
            timedelta(seconds=settings.scheduler_report_alerts_interval_s),
            lambda: jobs.run_report_alerts(
                session_factory, client, clock, reader=reader, settings=settings,
                directory=directory,
            ),
            True, timeout,
        ),
    ]  # fmt: skip


async def main(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    ro_engine = create_async_engine(settings.litellm_database_url)
    client = LiteLLMClient(settings.litellm_base_url, settings.litellm_master_key)
    clock, stop = SystemClock(), asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    try:
        todo = build_jobs(
            settings, sf, client, LiteLLMReader(ro_engine), FixtureDirectory(settings.seed_file),
            clock,
        )  # fmt: skip
        await run_loop(todo, sf, clock, stop=stop)
    finally:
        await client.aclose()
        await ro_engine.dispose()
        await engine.dispose()
