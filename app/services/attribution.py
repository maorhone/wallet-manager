"""Usage attribution (SPEC §7.3.1): base cap first, then shares by earliest expiry."""

import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import GrantShare, SpendSnapshot
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


async def displayed_used(db, upn: str, base_cap: Decimal, spend: Decimal) -> dict[int, Decimal]:
    """Display attribution from one period's snapshot spend; closed shares show what they stored."""
    shares = list(await db.scalars(select(GrantShare).where(GrantShare.upn == upn)))
    out = {s.id: s.used for s in shares if s.status == "closed"}
    out |= attribute(
        spend, base_cap,
        [ShareSpan(s.id, s.amount, s.expires_at) for s in shares if s.status == "active"],
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
            start, _ = month_bounds(periods[0], tz)
            end, _ = month_bounds(current, tz)
            for r in await reader.spend_rows(start, end):
                if r.team_id == team_id and r.user == upn:
                    key = r.day.isoformat()[:7]
                    out[key] = out.get(key, ZERO) + r.spend
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
