"""Rate card (SoT D-049/D-054): metered usage value = tokens x rate per model, never a debit."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.errors import invalid_request
from app.models import RateCard, SpendSnapshot
from app.permissions import Principal, can_manage_platform
from app.schemas.platform import RateCardIn
from app.services.history import values_as_of
from app.services.spend import month_bounds
from app.writes import WriteContext


def _view(r: RateCard) -> dict:
    return {
        "model": r.model,
        "input_per_1m": r.input_per_1m,
        "output_per_1m": r.output_per_1m,
        "currency": r.currency,
    }


async def list_rates(db) -> list[dict]:
    return [_view(r) for r in await db.scalars(select(RateCard).order_by(RateCard.model))]


async def set_rates(ctx: WriteContext, principal: Principal, body: RateCardIn) -> list[dict]:
    role = can_manage_platform(principal)
    models = [r.model for r in body.rates]
    if len(set(models)) != len(models):
        raise invalid_request("Duplicate model in rate card", {"field": "rates"})
    existing = {r.model: r for r in await ctx.db.scalars(select(RateCard))}
    entities = []
    for item in body.rates:
        row = existing.get(item.model)
        before = _view(row) if row else None
        if row is None:
            row = existing[item.model] = RateCard(model=item.model)
            ctx.db.add(row)
        row.input_per_1m, row.output_per_1m = item.input_per_1m, item.output_per_1m
        row.currency = item.currency
        row.effective_from = datetime.now(UTC)
        after = _view(row)
        ctx.audit("rate_card", item.model, before, after, role)
        entities.append({"entity": "rate_card", "id": item.model, "values": after})
    return entities


async def _rates_at(db, current: dict, at: datetime) -> dict:
    """Rates in force at ``at`` (D-049: priced by the consumption date), from the audited history.

    The first price ever set also covers earlier usage (the card is set up after usage began)."""
    past = await values_as_of(
        db, "rate_card",
        {m: {"input_per_1m": r.input_per_1m, "output_per_1m": r.output_per_1m}
         for m, r in current.items()},
        at,
    )  # fmt: skip
    return {m: SimpleNamespace(**v) for m, v in past.items()}


def _value(rows, rates) -> Decimal | None:
    """Tokens x rate; ``None`` when token usage has no model mapping or no rate (D-049)."""
    total = Decimal(0)
    for r in rows:
        if not (r.prompt_tokens or r.completion_tokens):
            continue
        rate = rates.get(r.model) if r.model is not None else None
        if rate is None:
            return None
        total += (
            r.prompt_tokens * rate.input_per_1m + r.completion_tokens * rate.output_per_1m
        ) / 1_000_000
    return total


async def _value_between(db, current: dict, rows, start: datetime, end: datetime):
    """Value ``rows`` consumed within [start, end): ``None`` if a rate they use changed inside
    the span, because aggregated data cannot say which side of the change the tokens fell on."""
    first, last = (
        await _rates_at(db, current, start),
        await _rates_at(db, current, end - timedelta(microseconds=1)),
    )
    for r in rows:
        if r.model in first and (first[r.model].input_per_1m, first[r.model].output_per_1m) != (
            last[r.model].input_per_1m, last[r.model].output_per_1m
        ):  # fmt: skip
            return None
    return _value(rows, last)


async def metered_value(
    db, scope_kind: str, scope_id, period: str, tz: str = "UTC"
) -> Decimal | None:
    """Value of the scope's tokens at the rate card; ``None`` if consumed tokens cannot be priced.

    Snapshot days are local days of ``tz`` (APP_TIMEZONE), each valued at its own rates, so a
    price change between days splits the month; a change inside a day (or inside a month that
    has no day rows) makes that span unavailable (D-049)."""
    rows = list(
        await db.scalars(
            select(SpendSnapshot).where(
                SpendSnapshot.scope_kind == scope_kind,
                SpendSnapshot.scope_id == str(scope_id),
                SpendSnapshot.period.like(f"{period}%"),
            )
        )
    )
    current = {r.model: r for r in await db.scalars(select(RateCard))}
    by_day: dict[str, list] = {}
    for r in rows:
        if len(r.period) > 7:
            by_day.setdefault(r.period, []).append(r)
    if not by_day:
        start, end = month_bounds(period[:7], ZoneInfo(tz))
        return await _value_between(
            db, current, [r for r in rows if r.period == period], start, end
        )
    total = Decimal(0)
    for day, day_rows in by_day.items():
        local = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=ZoneInfo(tz))
        start = local.astimezone(UTC)
        end = (local.replace(tzinfo=None) + timedelta(days=1)).replace(tzinfo=ZoneInfo(tz))
        value = await _value_between(db, current, day_rows, start, end.astimezone(UTC))
        if value is None:
            return None
        total += value
    return total
