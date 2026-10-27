"""Allocation values as they stood at a past moment, rebuilt from ``audit_log``."""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

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
    db: AsyncSession,
    entity: str,
    current: dict[str, dict],
    at: datetime | None,
    also: dict[str, dict[str, str]] | None = None,
    compensated: tuple[str, str] | None = None,
) -> dict[str, dict]:
    """``current`` is ``{id: {field: live value}}``; returns the same shape as of ``at``.

    Per field: the latest audit ``after`` at or before ``at``, else the earliest later audit
    ``before``, else the live value. ``at=None`` means now. ``also`` merges the audits of other
    entities into one chronological history: ``{entity: {their field: our field}}``.
    """
    if at is None or not current:
        return current
    renames = {entity: {}, **(also or {})}
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.entity.in_(list(renames)), AuditLog.entity_id.in_(list(current)))
        .order_by(AuditLog.at, AuditLog.id)
    )
    audits: dict[str, list[SimpleNamespace]] = {}
    for r in rows:
        names = renames[r.entity]
        audits.setdefault(r.entity_id, []).append(
            SimpleNamespace(
                at=r.at,
                before={names.get(k, k): v for k, v in (r.before or {}).items()},
                after={names.get(k, k): v for k, v in (r.after or {}).items()},
            )
        )
    if compensated:  # a cancelled transfer's restored totals, keyed "kind:id" in the audit
        kind, field = compensated
        comp = await db.scalars(select(AuditLog).where(AuditLog.entity == "transfer_compensation"))
        for r in comp:
            for key, value in (r.after or {}).items():
                if key.startswith(f"{kind}:") and key.split(":", 1)[1] in current:
                    audits.setdefault(key.split(":", 1)[1], []).append(
                        SimpleNamespace(
                            at=r.at, before={field: (r.before or {}).get(key)}, after={field: value}
                        )
                    )
        for items in audits.values():
            items.sort(key=lambda a: a.at)
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
    # request-approved increases are audited on the initiative itself, transfers on their own
    # entity: one chronological history of the budget (r12 B3)
    past = await values_as_of(
        db, "transfer_initiative", live, at, also={"initiative": {"budget": "total"}},
        compensated=("initiative", "total"),
    )  # fmt: skip
    return {int(i): v["total"] for i, v in past.items()}
