"""NFR7 Prometheus metrics. Gauges are refreshed on scrape from the database."""

from datetime import UTC, datetime

from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import LitellmWrite, SpendSnapshot

REGISTRY = CollectorRegistry()
OUTBOX_DEPTH = Gauge("wm_outbox_depth", "Outbox rows by status", ["status"], registry=REGISTRY)
VALIDATION_REJECTIONS = Counter(
    "wm_validation_rejections", "Rejected writes by error code", ["code"], registry=REGISTRY
)
SNAPSHOT_AGE = Gauge(
    "wm_spend_snapshot_age_seconds", "Age of the newest spend snapshot", registry=REGISTRY
)
OVERALLOCATION = Gauge(
    "wm_overallocation_findings", "Findings of the last over-allocation check", registry=REGISTRY
)
CONCURRENCY_PEAK = Gauge(
    "wm_concurrency_peak", "Yesterday's peak concurrent requests", ["scope"], registry=REGISTRY
)
BLOCKED_EMPLOYEES = Gauge(
    "wm_blocked_employees", "Distinct employees at a base or model cap yesterday", registry=REGISTRY
)
LIFECYCLE_ACTIONS = Counter(
    "wm_lifecycle_actions", "Member lifecycle actions by kind", ["action"], registry=REGISTRY
)
ONBOARDING_CALLS = Counter(
    "wm_onboarding_calls", "POST /onboarding calls by resulting status", ["status"],
    registry=REGISTRY,
)  # fmt: skip
OUTBOX_HELD = Gauge(
    "wm_outbox_held", "Pending outbox rows waiting for a row they depend on", registry=REGISTRY
)
SCHEDULER_RUNS = Counter(
    "wm_scheduler_job_runs", "Scheduler job runs by outcome (ok, error, timeout, skipped)",
    ["job", "outcome"], registry=REGISTRY,
)  # fmt: skip
SCHEDULER_LAST_SUCCESS = Gauge(
    "wm_scheduler_job_last_success_timestamp", "Unix time of the last successful job run", ["job"],
    registry=REGISTRY,
)  # fmt: skip
STATUSES = ("pending", "applied", "failed")
RECONCILE_DRIFT = Counter(
    "wm_reconcile_drift", "Detected drift", ["object", "field"], registry=REGISTRY
)
RECONCILE_ALERTS = Counter("wm_reconcile_alerts", "Repeated drift alerts", registry=REGISTRY)
RECONCILE_LAST_RUN = Gauge(
    "wm_reconcile_last_run_timestamp", "Last successful reconciliation", registry=REGISTRY
)


async def outbox_counts(db: AsyncSession) -> dict[str, int]:
    rows = (
        await db.execute(select(LitellmWrite.status, func.count()).group_by(LitellmWrite.status))
    ).all()
    return {s: dict(rows).get(s, 0) for s in STATUSES}


async def render(db: AsyncSession) -> bytes:
    for status, n in (await outbox_counts(db)).items():
        OUTBOX_DEPTH.labels(status).set(n)
    dep = aliased(LitellmWrite)
    OUTBOX_HELD.set(
        await db.scalar(
            select(func.count())
            .select_from(LitellmWrite)
            .join(dep, LitellmWrite.depends_on == dep.id)
            .where(LitellmWrite.status == "pending", dep.status != "applied")
        )
    )
    taken = await db.scalar(select(func.max(SpendSnapshot.taken_at)))
    SNAPSHOT_AGE.set((datetime.now(UTC) - taken).total_seconds() if taken else float("nan"))
    return generate_latest(REGISTRY)
