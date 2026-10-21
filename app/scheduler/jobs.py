"""Scheduler jobs (SPEC §5.1): grant and share activation and expiry, FR-P4 recompute.

Every job takes ``clock``; nothing here reads the wall clock. Writes go through ``WriteContext`` as
actor ``system:scheduler``. The request id of each event is deterministic, so a rerun that finds
its audit row writes nothing; rows are also re-checked under ``FOR UPDATE SKIP LOCKED``, so two
replicas never close the same row twice even without the job lock.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app import metrics
from app.clock import Clock
from app.config import Settings
from app.db import LockKind
from app.directory import Directory
from app.models import AuditLog, Employee, Grant, GrantShare
from app.permissions import ADMIN, Binding, Principal
from app.services import grants as grants_service
from app.services import notify_scan, reports
from app.services import shares as shares_service
from app.services.notifications import notify, upns_of
from app.services.overallocation import scheduled_check
from app.services.owners import holders
from app.services.reconcile import run_reconcile  # noqa: F401
from app.writes import WriteContext

ACTOR, ROLE = "system:scheduler", "scheduler"
_NS = uuid.UUID("0c7d8f56-3b0e-4c53-9d36-6a1f2b9e4a71")


@dataclass
class GrantsResult:
    activated: int = 0
    shares_closed: int = 0
    grants_closed: int = 0
    members_recomputed: int = 0


def request_id(entity: str, entity_id, event: str) -> str:
    return str(uuid.uuid5(_NS, f"grants:{entity}:{entity_id}:{event}"))


async def _begin(db, settings: Settings, entity: str, entity_id, event: str, reason: str):
    """A scheduler WriteContext, or None when this event was already written (replay)."""
    rid = request_id(entity, entity_id, event)
    if await db.scalar(select(AuditLog.id).where(AuditLog.request_id == rid).limit(1)):
        return None
    return WriteContext(db, settings, ACTOR, rid, reason)


async def _activate(sf, settings: Settings, grant_id: int, group_id: int, now: datetime) -> bool:
    async with sf() as db:
        ctx = await _begin(db, settings, "grant", grant_id, "activate", "grant start time reached")
        if ctx is None:
            return False
        await ctx.lock(LockKind.GROUP, group_id)
        g = await db.scalar(
            select(Grant).where(
                Grant.id == grant_id, Grant.status == "pending", Grant.starts_at <= now
            ).with_for_update(skip_locked=True)
        )  # fmt: skip
        if g is None:
            return False
        before, g.status = grants_service._values(g), "active"
        grants_service._audit_grant(ctx, g, before, ROLE)
        await grants_service._team_update(ctx, g, ROLE, [])
        await db.commit()
        return True


async def _expire_share(
    sf, settings: Settings, share_id: int, group_id: int, now: datetime, client, reader
) -> str | None:
    """Close one expired share; returns its member's upn, or None if it was already closed."""
    async with sf() as db:
        ctx = await _begin(db, settings, "share", share_id, "expiry", "share expired")
        if ctx is None:
            return None
        await ctx.lock(LockKind.GROUP, group_id)
        s = await db.scalar(
            select(GrantShare).where(
                GrantShare.id == share_id, GrantShare.status == "active",
                GrantShare.expires_at <= now,
            ).with_for_update(skip_locked=True)
        )  # fmt: skip
        if s is None:
            return None
        await shares_service.close_shares(ctx, [s], now, "expiry", client, reader, ROLE)
        await db.commit()
        return s.upn


async def _expire_grant(
    sf, settings: Settings, grant_id: int, group_id: int, now: datetime, client, reader
) -> list[str] | None:
    """Close one expired grant and its open shares; returns the affected members' upns."""
    async with sf() as db:
        ctx = await _begin(db, settings, "grant", grant_id, "expiry", "grant expired")
        if ctx is None:
            return None
        await ctx.lock(LockKind.GROUP, group_id)
        g = await db.scalar(
            select(Grant).where(
                Grant.id == grant_id, Grant.status == "active", Grant.expires_at <= now
            ).with_for_update(skip_locked=True)
        )  # fmt: skip
        if g is None:
            return None
        upns = list(
            await db.scalars(
                select(GrantShare.upn).where(
                    GrantShare.grant_id == g.id, GrantShare.status == "active"
                )
            )
        )
        await grants_service._close_grant(ctx, g, now, ROLE, "expiry", client, reader)
        await db.commit()
        return upns


async def run_grants(sf, client, clock: Clock, *, reader, settings: Settings) -> GrantsResult:
    now, out, touched = clock.now(), GrantsResult(), set()

    async def ids(*columns, where, order):
        async with sf() as db:
            return (await db.execute(select(*columns).where(*where).order_by(*order))).all()

    for gid, group_id in await ids(
        Grant.id, Grant.group_id, where=(Grant.status == "pending", Grant.starts_at <= now),
        order=(Grant.starts_at, Grant.id),
    ):  # fmt: skip
        out.activated += await _activate(sf, settings, gid, group_id, now)

    for sid, group_id in await ids(
        GrantShare.id, Grant.group_id, where=(
            GrantShare.grant_id == Grant.id, GrantShare.status == "active",
            GrantShare.expires_at <= now,
        ), order=(GrantShare.expires_at, GrantShare.id),
    ):  # fmt: skip
        if upn := await _expire_share(sf, settings, sid, group_id, now, client, reader):
            out.shares_closed += 1
            touched.add(upn)

    for gid, group_id in await ids(
        Grant.id, Grant.group_id, where=(Grant.status == "active", Grant.expires_at <= now),
        order=(Grant.expires_at, Grant.id),
    ):  # fmt: skip
        if (
            upns := await _expire_grant(sf, settings, gid, group_id, now, client, reader)
        ) is not None:
            out.grants_closed += 1
            touched.update(upns)

    out.members_recomputed = len(touched)
    return out


@dataclass
class NotifyResult:
    created: dict[str, int]


async def run_notifications(
    sf, client, clock: Clock, *, settings: Settings, directory: Directory
) -> NotifyResult:
    """FR-N1 threshold kinds; the dedupe keys make a rerun in the same period a no-op."""
    async with sf() as db:
        created = await notify_scan.scan(db, directory, settings, clock.now())
        await db.commit()
    return NotifyResult(dict(created))


async def run_report_alerts(
    sf, client, clock: Clock, *, reader, settings: Settings, directory: Directory
) -> NotifyResult:
    """Daily pilot input for D6, plus FR-V2 alerts; all windows use the injected clock."""
    await scheduled_check(sf, reader, clock=clock, settings=settings, directory=directory)
    day = clock.now().astimezone(ZoneInfo(settings.app_timezone)).date() - timedelta(days=1)
    lo, hi = reports.report_window(day, day, settings.app_timezone, 31)
    peaks = await reader.concurrency_peaks(lo, hi, timezone=settings.app_timezone)
    global_peak = max((r.peak for r in peaks if r.user is None), default=0)
    by_user = {r.user: r for r in peaks if r.user is not None}
    async with sf() as db:
        members = []
        for member in await db.scalars(
            select(Employee).where(Employee.status != "left", Employee.group_id.is_not(None))
        ):
            peak = by_user.get(member.upn)
            if (
                peak is not None
                and member.max_parallel is not None
                and peak.peak >= member.max_parallel
            ):
                members.append(
                    dict(
                        upn=member.upn,
                        display_name=member.display_name,
                        peak=peak.peak,
                        at=peak.at.isoformat(),
                        max_parallel=member.max_parallel,
                    )
                )
        members.sort(key=lambda m: (-m["peak"], m["upn"]))
        # Reuse FR-R5's base/model exhaustion and local-month snapshot calculation.
        principal = Principal(ACTOR, ROLE, [Binding(ADMIN, "global", None)])
        blocked = await reports.blocked(db, principal, settings, clock, "global", None, day, day)
        blocked_count = len({r["upn"] for r in blocked["rows"]})
        created = 0
        if members:
            admins = upns_of(await holders(db, directory, ADMIN, "global", None))
            created = await notify(
                db,
                admins,
                "concurrency_at_limit",
                None,
                None,
                {"day": day.isoformat(), "count": len(members), "members": members[:100]},
                f"concurrency_at_limit:{day}",
                settings=settings,
            )
        await db.commit()
    metrics.CONCURRENCY_PEAK.labels("global").set(global_peak)
    metrics.BLOCKED_EMPLOYEES.set(blocked_count)
    return NotifyResult({"concurrency_at_limit": created})
