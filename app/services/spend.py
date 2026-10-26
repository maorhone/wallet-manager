"""Spend snapshot (SPEC §12.9, §5.4): LiteLLM SpendLogs -> ``spend_snapshot`` for the UI."""

import logging
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import (
    Department,
    Employee,
    Grp,
    LitellmWrite,
    Organization,
    ProductionSystem,
    ServiceKey,
    SpendSnapshot,
)

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


async def _scope_maps(db) -> tuple:
    groups = {g.litellm_team_id: g for g in await db.scalars(select(Grp))}
    systems = {s.litellm_team_id: s for s in await db.scalars(select(ProductionSystem))}
    keys = {
        k.litellm_token_hash: k.id
        for k in await db.scalars(select(ServiceKey))
        if k.litellm_token_hash
    }
    # Frozen deletion payloads preserve former hashes after rotation, including pending
    # deletions, so a refresh never drops spend from an earlier key generation.
    for write in await db.scalars(
        select(LitellmWrite).where(
            LitellmWrite.entity == "service_key",
            LitellmWrite.endpoint == "/key/delete",
        )
    ):
        for token in (write.payload or {}).get("keys", []):
            keys[token] = int(write.entity_id)
    member_group = dict((await db.execute(select(Employee.upn, Employee.group_id))).all())
    return groups, systems, keys, member_group


def _aggregate(rows, period: str, maps: tuple):
    groups, systems, keys, member_group = maps
    totals: dict[tuple, Decimal] = defaultdict(Decimal)
    tokens: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        group = groups.get(r.team_id)
        if group is not None:
            scopes = [("group", str(group.id)), ("department", str(group.department_id))]
            if r.user and member_group.get(r.user) == group.id:  # membership spend only
                scopes.append(("employee", r.user))
        else:
            system = systems.get(r.team_id)
            scopes = [("system", str(system.id))] if system is not None else []
        if r.api_key in keys:
            scopes.append(("service_key", str(keys[r.api_key])))
        for kind, scope_id in scopes:
            day = r.day.isoformat()
            # Reader buckets in APP_TIMEZONE, including the local month boundary.
            for p in (period, day) if day.startswith(period) else (period,):
                totals[(kind, scope_id, p, None)] += r.spend
                if r.model_group is not None:
                    totals[(kind, scope_id, p, r.model_group)] += r.spend
                tok = tokens[(kind, scope_id, p, r.model_group)]
                tok[0] += r.prompt_tokens
                tok[1] += r.completion_tokens
    return totals, tokens


async def _window_start(db, now: datetime) -> datetime:
    """Earliest instant any configured budget window can still cover (B1a2).

    LiteLLM resets a budget every ``budget_duration``; a "2mo" budget still holds last month's
    spend, so money decisions read back over the longest configured window (over-counting a
    shorter window is the safe direction).
    """
    periods = set()
    for col in (
        Organization.budget_period,
        Department.budget_period,
        Department.prod_budget_period,
    ):
        periods.update(await db.scalars(select(col)))
    months, longest = 1, timedelta()
    unit_span = {"s": timedelta(seconds=1), "m": timedelta(minutes=1), "h": timedelta(hours=1),
                 "d": timedelta(days=1), "mo": timedelta(days=31)}  # fmt: skip
    for p in periods:
        m = re.fullmatch(r"(\d+)(s|m|h|d|mo)", p or "")
        if m is None:
            raise SpendUnavailable  # an unreadable window is never treated as the month
        n, unit = int(m[1]), m[2]
        if unit == "mo":
            months = max(months, n)
        longest = max(longest, n * unit_span[unit])  # a window may begin mid-month (r7 B1a2)
    year, month = now.year, now.month - (months - 1)
    year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
    start = datetime(year, month, 1, tzinfo=now.tzinfo).astimezone(UTC)
    return min(start, (now - longest).astimezone(UTC))


async def fresh_spend(db, reader, now: datetime) -> "Spend":
    """Spend read straight from LiteLLM at call time (not the 5-minute UI cache).

    Money decisions (transfers) use this; a missing reader or an unreadable database raises
    ``SpendUnavailable`` so nothing is ever treated as zero spend.
    """
    if reader is None:
        raise SpendUnavailable
    period = now.strftime("%Y-%m")
    start, end = month_bounds(period, now.tzinfo)
    start = min(start, await _window_start(db, now))
    try:
        rows = await reader.spend_rows(start, end, timezone=str(now.tzinfo))
    except Exception:
        raise SpendUnavailable from None
    totals, _ = _aggregate(rows, period, await _scope_maps(db))
    taken = now.astimezone(UTC)
    return Spend(
        [
            SpendSnapshot(scope_kind=k, scope_id=i, period=p, model=m, spend=s, taken_at=taken)
            for (k, i, p, m), s in totals.items()
        ],
        period,
    )


class SpendUnavailable(Exception):
    """Fresh LiteLLM spend could not be read."""


async def take_snapshot(sf: async_sessionmaker, reader, now: datetime) -> None:
    """Refresh the current period, and the previous one if it has no rows.

    ``now`` carries APP_TIMEZONE. An unreachable LiteLLM database keeps the old rows.
    """
    current = now.strftime("%Y-%m")
    async with sf() as db:
        maps = await _scope_maps(db)
        have_previous = await db.scalar(
            select(SpendSnapshot.period).where(SpendSnapshot.period == previous_period(current))
        )
    periods = [current] if have_previous else [current, previous_period(current)]
    for period in periods:
        start, end = month_bounds(period, now.tzinfo)
        try:
            rows = await reader.spend_rows(start, end, timezone=str(now.tzinfo))
        except Exception:
            log.warning("spend snapshot: LiteLLM database unreachable; keeping old rows")
            return
        totals, tokens = _aggregate(rows, period, maps)
        async with sf() as db, db.begin():
            await db.execute(
                delete(SpendSnapshot).where(
                    (SpendSnapshot.period == period) | SpendSnapshot.period.like(f"{period}-%")
                )
            )
            taken = now.astimezone(UTC)
            db.add_all(
                SpendSnapshot(
                    scope_kind=k,
                    scope_id=i,
                    period=p,
                    model=m,
                    spend=s,
                    taken_at=taken,
                    prompt_tokens=tokens[(k, i, p, m)][0],
                    completion_tokens=tokens[(k, i, p, m)][1],
                )  # fmt: skip
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
