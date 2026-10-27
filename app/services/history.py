"""Allocation values as they stood at a past moment, rebuilt from ``audit_log``."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Initiative


def _like(current, value):
    """Audit JSON carries floats; give back the type of the live value."""
    if isinstance(current, Decimal) and value is not None:
        return Decimal(str(value))
    if isinstance(current, dict) and isinstance(value, dict):
        return {k: Decimal(str(v)) for k, v in value.items()}
    return value


async def values_as_of(
    db: AsyncSession, entity: str, current: dict[str, dict], at: datetime | None
) -> dict[str, dict]:
    """``current`` is ``{id: {field: live value}}``; returns the same shape as of ``at``.

    Per field: the latest audit ``after`` at or before ``at``, else the earliest later audit
    ``before``, else the live value. ``at=None`` means now.
    """
    if at is None or not current:
        return current
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.entity == entity, AuditLog.entity_id.in_(list(current)))
        .order_by(AuditLog.at, AuditLog.id)
    )
    audits: dict[str, list[AuditLog]] = {}
    for r in rows:
        audits.setdefault(r.entity_id, []).append(r)
    out: dict[str, dict] = {}
    for id_, fields in current.items():
        out[id_] = {}
        for field, live in fields.items():
            value, found = live, False
            for r in audits.get(id_, []):
                if r.at <= at and field in (r.after or {}):
                    value, found = r.after[field], True  # keeps the latest
            if not found:
                later = next(
                    (r for r in audits.get(id_, []) if r.at > at and field in (r.before or {})),
                    None,
                )
                value = later.before[field] if later else live
            out[id_][field] = live if value is live else _like(live, value)
    return out


async def initiative_budgets_as_of(
    db: AsyncSession, group_id: int, at: datetime | None
) -> dict[int, Decimal]:
    """Budgets of the group's active initiatives as of ``at``: the ones created by then, with
    the budget moved by transfers up to then (``at=None``: live). One source for the wallet
    totals and the overview rows (r11 B3)."""
    query = select(Initiative).where(Initiative.group_id == group_id, Initiative.status == "active")
    if at is not None:
        query = query.where(Initiative.created_at <= at)
    live = {str(i.id): {"total": i.budget} for i in await db.scalars(query.order_by(Initiative.id))}
    past = await values_as_of(db, "transfer_initiative", live, at)
    return {int(i): v["total"] for i, v in past.items()}
