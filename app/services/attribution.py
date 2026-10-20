"""Usage attribution (SPEC §7.3.1): base cap first, then shares by earliest expiry."""

import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.models import AuditLog, Employee, Grant, GrantShare, SpendSnapshot
from app.services.spend import month_bounds

log = logging.getLogger(__name__)
ZERO = Decimal(0)


@dataclass(frozen=True)
class ShareSpan:
    id: int
    amount: Decimal
    expires_at: datetime
    used: Decimal | None = None  # closed share: the stored value


def attribute(
    member_spend: Decimal, base_cap: Decimal, shares: Sequence[ShareSpan]
) -> dict[int, Decimal]:
    """used(s) = clamp(spend − base − Σ used(earlier), 0, s.amount), earlier = (expires_at, id)."""
    left = max(member_spend - base_cap, ZERO)
    out: dict[int, Decimal] = {}
    for s in sorted(shares, key=lambda s: (s.expires_at, s.id)):
        out[s.id] = s.used if s.used is not None else min(left, s.amount)
        left = max(left - out[s.id], ZERO)
    return out


def attribute_periods(
    spend_by_period: Mapping[str, Decimal],
    base_cap: Decimal,
    shares_by_period: Mapping[str, Sequence[ShareSpan]],
) -> dict[int, Decimal]:
    """Attribute each period on its own (the base cap resets), then sum per share, clamped."""
    total: dict[int, Decimal] = defaultdict(Decimal)
    spans: dict[int, ShareSpan] = {}
    for period, shares in shares_by_period.items():
        spans.update({s.id: s for s in shares})
        for sid, used in attribute(spend_by_period.get(period, ZERO), base_cap, shares).items():
            total[sid] += used
    return {
        sid: spans[sid].used if spans[sid].used is not None else min(used, spans[sid].amount)
        for sid, used in total.items()
    }


async def membership_shares(db, upn: str, group_id: int):
    """Shares in this membership, excluding earlier visits to the same team.

    Lifecycle audit timestamps are the membership boundaries. Closed shares from before the
    latest move/place belong to the old membership's spend, even when the team id is reused.
    """
    joined = await db.scalar(
        select(func.max(AuditLog.at)).where(
            AuditLog.entity == "employee",
            AuditLog.entity_id == upn,
            AuditLog.after["action"].astext.in_(("move", "place")),
        )
    )
    rows = (
        await db.execute(
            select(GrantShare, Grant)
            .join(Grant, Grant.id == GrantShare.grant_id)
            .where(GrantShare.upn == upn, Grant.group_id == group_id)
        )
    ).all()
    if joined is not None:
        closed_here = set(
            await db.scalars(
                select(AuditLog.entity_id).where(
                    AuditLog.entity == "grant_share",
                    AuditLog.entity_id.in_([str(s.id) for s, _ in rows]),
                    AuditLog.at >= joined,
                    AuditLog.after["status"].astext == "closed",
                )
            )
        )
        rows = [(s, g) for s, g in rows if s.status == "active" or str(s.id) in closed_here]
    return rows, joined


async def displayed_used(
    db,
    upn: str,
    base_cap: Decimal,
    spend: Decimal,
    period: str | None = None,
    timezone: str = "Asia/Jerusalem",
) -> dict[int, Decimal]:
    """Display attribution from one period's snapshot spend; closed shares show what they stored."""
    rows = (
        await db.execute(
            select(GrantShare, Grant)
            .join(Grant, Grant.id == GrantShare.grant_id)
            .where(GrantShare.upn == upn)
        )
    ).all()
    emp = await db.get(Employee, upn)
    eligible, _ = await membership_shares(db, upn, emp.group_id)
    start, end = month_bounds(
        period or datetime.now(UTC).astimezone(ZoneInfo(timezone)).strftime("%Y-%m"),
        ZoneInfo(timezone),
    )
    out = {s.id: s.used for s, _ in rows if s.status == "closed"}
    out |= attribute(
        spend, base_cap,
        [ShareSpan(s.id, s.amount, s.expires_at or g.expires_at,
                   s.used if s.status == "closed" else None)
         for s, g in eligible
         if g.starts_at < end and (s.expires_at or g.expires_at) > start],
    )  # fmt: skip
    return out


def periods_between(start: datetime, end: datetime, tz) -> list[str]:
    """``YYYY-MM`` periods (in ``tz``) from ``start`` through ``end``."""
    first, last = start.astimezone(tz), end.astimezone(tz)
    out, (y, m) = [], (first.year, first.month)
    while (y, m) <= (last.year, last.month):
        out.append(f"{y}-{m:02d}")
        y, m = y + m // 12, m % 12 + 1
    return out


async def member_spend_by_period(
    db, client, reader, upn: str, team_id: str, since: datetime, now: datetime, tz
) -> dict[str, Decimal]:
    """Employee spend per period from ``since`` to ``now``.

    Current period: LiteLLM team membership spend. Earlier periods: SpendLogs through the
    read-only reader. If LiteLLM is unreachable: the ``spend_snapshot`` rows (WARNING logged).
    """
    periods = periods_between(since, now, tz)
    current = periods[-1]
    try:
        info = await client.team_info(team_id)
        out = {current: ZERO}
        for m in info.team_memberships:
            if m.user_id == upn:
                out[current] = Decimal(str(m.spend))
        if len(periods) > 1:
            for period in periods[:-1]:
                start, end = month_bounds(period, tz)
                out[period] = sum(
                    (
                        r.spend
                        for r in await reader.spend_rows(start, end)
                        if r.team_id == team_id and r.user == upn
                    ),
                    ZERO,
                )
        return out
    except Exception:
        log.warning("attribution: LiteLLM unreachable; using the spend snapshot for %s", upn)
    rows = await db.scalars(
        select(SpendSnapshot).where(
            SpendSnapshot.scope_kind == "employee", SpendSnapshot.scope_id == upn,
            SpendSnapshot.model.is_(None), SpendSnapshot.period.in_(periods),
        )
    )  # fmt: skip
    return {p: ZERO for p in periods} | {r.period: r.spend for r in rows}
