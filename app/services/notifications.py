"""In-app notifications (SPEC §7.9 FR-N1, FR-N2): ``notify``, recipients and the /me helpers.

Kept free of wallet views so the domain services (grants, lifecycle, onboarding) can import it.
"""

import asyncio
import logging

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import notify_email
from app.config import Settings
from app.directory import Directory
from app.errors import invalid_request
from app.models import Notification, NotificationPref
from app.permissions import ADMIN, HEAD, REP
from app.services.owners import holders
from app.writes import plain

log = logging.getLogger("app.notifications")

# kind -> critical (a critical kind cannot be muted, FR-N2)
KINDS: dict[str, bool] = {
    "employee_80": False, "employee_100": True, "group_80": False, "group_100": True,
    "grant_expiring": False, "grant_underused": False, "pending_placement": False,
    "member_moved_in": False, "outbox_failed": True, "grant_received": False,
    "drift_repeated": True, "reconcile_missing": True,
    "overallocation": True, "concurrency_at_limit": False,
}  # fmt: skip


async def notify(
    db: AsyncSession, upns, kind: str, scope_kind: str | None, scope_id: int | None,
    payload: dict, key: str, *, settings: Settings | None = None,
) -> int:  # fmt: skip
    """Insert one notification per recipient (muted non-critical kinds and repeats are skipped).

    ``key`` is the dedupe key (unique per ``upn`` and ``kind``); returns how many rows were new.
    With ``settings.notify_email_enabled`` each new row is also mailed (a failure only logs).
    """
    upns = sorted(set(upns))
    muted = set()
    if upns and not KINDS[kind]:
        muted = set(
            await db.scalars(
                select(NotificationPref.upn).where(
                    NotificationPref.upn.in_(upns), NotificationPref.kind == kind,
                    NotificationPref.muted.is_(True),
                )
            )
        )  # fmt: skip
    created = 0
    for upn in upns:
        if upn in muted:
            continue
        row = (
            insert(Notification)
            .values(
                upn=upn, kind=kind, scope_kind=scope_kind, scope_id=scope_id,
                payload=plain({**payload, "key": key}),
            )
            .on_conflict_do_nothing()
            .returning(Notification.id)
        )  # fmt: skip
        if await db.scalar(row) is None:
            continue
        created += 1
        if settings is not None and settings.notify_email_enabled:
            try:
                await asyncio.to_thread(
                    notify_email.send, settings, upn, f"Wallet Manager: {kind}", str(payload)
                )
            except Exception:
                log.exception("notification email to %s failed", upn)
    return created


def upns_of(people: list[dict]) -> set[str]:
    return {p["upn"] for p in people}


async def heads_of(db, directory: Directory, group_id: int) -> set[str]:
    return upns_of(await holders(db, directory, HEAD, "group", group_id))


async def reps_or_admins(db, directory: Directory, department_id: int | None) -> set[str]:
    """The department's representatives, or platform admins when the department is unknown."""
    if department_id is None:
        return upns_of(await holders(db, directory, ADMIN, "global", None))
    return upns_of(await holders(db, directory, REP, "department", department_id))


# --- the /me endpoints -------------------------------------------------------------------------


def prefs_view(muted: set[str]) -> list[dict]:
    return [{"kind": k, "critical": c, "muted": k in muted} for k, c in KINDS.items()]


async def _muted(db, upn: str) -> set[str]:
    rows = await db.scalars(
        select(NotificationPref.kind).where(
            NotificationPref.upn == upn, NotificationPref.muted.is_(True)
        )
    )
    return set(rows)


async def list_for(db, upn: str, unread: bool) -> dict:
    q = select(Notification).where(Notification.upn == upn)
    if unread:
        q = q.where(Notification.read_at.is_(None))
    rows = await db.scalars(q.order_by(Notification.id.desc()).limit(100))
    count = await db.scalar(
        select(func.count()).select_from(Notification)
        .where(Notification.upn == upn, Notification.read_at.is_(None))
    )  # fmt: skip
    items = [
        {
            "id": n.id, "kind": n.kind, "critical": KINDS.get(n.kind, False),
            "scope_kind": n.scope_kind, "scope_id": n.scope_id, "payload": n.payload,
            "created_at": n.created_at, "read_at": n.read_at,
        }
        for n in rows
    ]  # fmt: skip
    return {"items": items, "unread_count": count, "prefs": prefs_view(await _muted(db, upn))}


async def mark_read(db, upn: str, notification_id: int, now) -> Notification | None:
    n = await db.get(Notification, notification_id)
    if n is None or n.upn != upn:
        return None
    if n.read_at is None:
        n.read_at = now
    return n


async def set_prefs(db, upn: str, changes: dict[str, bool]) -> list[dict]:
    for kind, muted in changes.items():
        if kind not in KINDS:
            raise invalid_request("Unknown notification kind", {"kind": kind})
        if muted and KINDS[kind]:
            raise invalid_request("A critical notification cannot be muted", {"kind": kind})
    for kind, muted in changes.items():
        await db.merge(NotificationPref(upn=upn, kind=kind, muted=muted))
    await db.flush()
    return prefs_view(await _muted(db, upn))
