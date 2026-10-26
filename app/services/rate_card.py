"""Rate card (SoT D-049/D-054): metered usage value = tokens x rate per model, never a debit."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

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


async def _rates_as_of(db, period: str) -> dict:
    """Rates that applied at the end of ``period`` (D-049: priced by the consumption date).

    A past month reads the audited rate history, so a later price change never rewrites it.
    The first price ever set also covers earlier months (the card is set up after usage began)."""
    rates = {r.model: r for r in await db.scalars(select(RateCard))}
    end = month_bounds(period[:7], UTC)[1]
    if end > datetime.now(UTC):
        return rates
    past = await values_as_of(
        db, "rate_card",
        {m: {"input_per_1m": r.input_per_1m, "output_per_1m": r.output_per_1m}
         for m, r in rates.items()},
        end,
    )  # fmt: skip
    return {m: SimpleNamespace(**v) for m, v in past.items()}


async def metered_value(db, scope_kind: str, scope_id, period: str) -> Decimal | None:
    """Value of the scope's tokens at the rate card; ``None`` if a consumed model has no rate."""
    rows = await db.scalars(
        select(SpendSnapshot).where(
            SpendSnapshot.scope_kind == scope_kind,
            SpendSnapshot.scope_id == str(scope_id),
            SpendSnapshot.period == period,
            SpendSnapshot.model.is_not(None),
        )
    )
    rates = await _rates_as_of(db, period)
    total = Decimal(0)
    for r in rows:
        if not (r.prompt_tokens or r.completion_tokens):
            continue
        rate = rates.get(r.model)
        if rate is None:
            return None
        total += (
            r.prompt_tokens * rate.input_per_1m + r.completion_tokens * rate.output_per_1m
        ) / 1_000_000
    return total
