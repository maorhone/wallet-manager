from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models import Grant, GrantShare
from app.outbox.desired import build

DANI = "dani@corp.example"
NOW = datetime(2026, 10, 20, tzinfo=UTC)


async def _grant(s, model=None) -> Grant:
    g = Grant(
        department_id=1, group_id=1, amount=1000, model=model, project_ref="P", purpose="x",
        starts_at=NOW - timedelta(days=1), expires_at=NOW + timedelta(days=60), status="active",
    )  # fmt: skip
    s.add(g)
    await s.flush()
    return g


def _share(g, amount, days, status="active"):
    return GrantShare(
        grant_id=g.id, upn=DANI, amount=amount, expires_at=NOW + timedelta(days=days), status=status
    )


async def test_temp_increase_sum_and_earliest_expiry(seeded, settings):
    async with seeded() as s:
        g1, g2, g3 = await _grant(s), await _grant(s), await _grant(s)
        s.add_all([_share(g1, 100, 20), _share(g2, 50, 5), _share(g3, 70, 1, "closed")])
        await s.commit()
        req = await build(s, settings, "employee", "/team/member_update", DANI)
    assert req.temp_budget_increase == 150
    assert req.temp_budget_expiry == NOW + timedelta(days=5)


async def test_no_shares_sends_zero_and_null_together(seeded, settings):
    async with seeded() as s:
        req = await build(s, settings, "employee", "/team/member_update", DANI)
    payload = req.payload()
    assert payload["temp_budget_increase"] == 0 and payload["temp_budget_expiry"] is None


async def test_model_share_raises_member_model_cap(seeded, settings):
    async with seeded() as s:
        g = await _grant(s, "gpt-4o")
        plain = await _grant(s)
        s.add_all([_share(g, 100, 20), _share(plain, 30, 20)])
        await s.commit()
        req = await build(s, settings, "employee", "/team/member_update", DANI)
    assert req.model_max_budget["gpt-4o"].budget_limit == float(Decimal(100))
    assert req.temp_budget_increase == 130
