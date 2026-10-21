"""Spend snapshot (SPEC §12.9, §5.4): LiteLLM SpendLogs -> ``spend_snapshot`` for the UI."""

import logging
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Employee, Grp, ProductionSystem, SpendSnapshot

log = logging.getLogger(__name__)


def month_bounds(period: str, tz) -> tuple[datetime, datetime]:
    """Calendar month ``YYYY-MM`` in ``tz`` as UTC [start, end)."""
    year, month = map(int, period.split("-"))
    start = datetime(year, month, 1, tzinfo=tz)
    end = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def previous_period(period: str) -> str:
    year, month = map(int, period.split("-"))
    return f"{year - (month == 1)}-{(month - 2) % 12 + 1:02d}"


async def take_snapshot(sf: async_sessionmaker, reader, now: datetime) -> None:
    """Refresh the current period, and the previous one if it has no rows.

    ``now`` carries APP_TIMEZONE. An unreachable LiteLLM database keeps the old rows.
    """
    current = now.strftime("%Y-%m")
    async with sf() as db:
        groups = {g.litellm_team_id: g for g in await db.scalars(select(Grp))}
        systems = {s.litellm_team_id: s for s in await db.scalars(select(ProductionSystem))}
        member_group = dict((await db.execute(select(Employee.upn, Employee.group_id))).all())
        have_previous = await db.scalar(
            select(SpendSnapshot.period).where(SpendSnapshot.period == previous_period(current))
        )
    periods = [current] if have_previous else [current, previous_period(current)]
    for period in periods:
        start, end = month_bounds(period, now.tzinfo)
        try:
            rows = await reader.spend_rows(start, end)
        except Exception:
            log.warning("spend snapshot: LiteLLM database unreachable; keeping old rows")
            return
        totals: dict[tuple, Decimal] = defaultdict(Decimal)
        for r in rows:
            group = groups.get(r.team_id)
            if group is not None:
                scopes = [("group", str(group.id)), ("department", str(group.department_id))]
                if r.user and member_group.get(r.user) == group.id:  # membership spend only
                    scopes.append(("employee", r.user))
            else:
                system = systems.get(r.team_id)
                scopes = [("system", str(system.id))] if system is not None else []
            for kind, scope_id in scopes:
                day = r.day.isoformat()
                # day rows stay inside the calendar month so periods never share a key
                for p in (period, day) if day.startswith(period) else (period,):
                    totals[(kind, scope_id, p, None)] += r.spend
                    totals[(kind, scope_id, p, r.model_group)] += r.spend
        async with sf() as db, db.begin():
            await db.execute(
                delete(SpendSnapshot).where(
                    (SpendSnapshot.period == period) | SpendSnapshot.period.like(f"{period}-%")
                )
            )
            taken = datetime.now(UTC)
            db.add_all(
                SpendSnapshot(scope_kind=k, scope_id=i, period=p, model=m, spend=s, taken_at=taken)
                for (k, i, p, m), s in totals.items()
            )


class Spend:
    """Snapshot reads for one period (``YYYY-MM``); missing rows mean zero."""

    def __init__(self, rows: list[SpendSnapshot], period: str):
        self.period = period
        self.taken_at = max((r.taken_at for r in rows), default=None)
        self._rows = {(r.scope_kind, r.scope_id, r.period, r.model): r.spend for r in rows}

    @classmethod
    async def load(cls, db, period: str) -> "Spend":
        rows = await db.scalars(select(SpendSnapshot).where(SpendSnapshot.period == period))
        return cls(list(rows), period)

    def used(self, kind: str, scope_id, model: str | None = None) -> Decimal:
        return self._rows.get((kind, str(scope_id), self.period, model), Decimal(0))

    def by_model(self, kind: str, scope_id) -> dict[str, Decimal]:
        return {
            m: s
            for (k, i, p, m), s in self._rows.items()
            if (k, i, p) == (kind, str(scope_id), self.period) and m is not None
        }
