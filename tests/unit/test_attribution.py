from datetime import UTC, datetime
from decimal import Decimal as D

from app.services.attribution import ShareSpan, attribute, attribute_periods


def _at(day: int) -> datetime:
    return datetime(2026, 10, day, tzinfo=UTC)


def test_base_first_then_earliest_expiry():
    shares = [ShareSpan(1, D(100), _at(20)), ShareSpan(2, D(100), _at(10))]
    assert attribute(D(1050), D(1000), shares) == {2: 50, 1: 0}
    assert attribute(D(1150), D(1000), shares) == {2: 100, 1: 50}


def test_clamp_zero_and_amount():
    shares = [ShareSpan(1, D(100), _at(10))]
    assert attribute(D(500), D(1000), shares) == {1: 0}
    assert attribute(D(5000), D(1000), shares) == {1: 100}


def test_closed_share_used_counts_as_earlier():
    shares = [ShareSpan(1, D(100), _at(5), used=D(60)), ShareSpan(2, D(100), _at(10))]
    assert attribute(D(1100), D(1000), shares) == {1: 60, 2: 40}


def test_period_reset_sums_periods():
    shares = {
        "2026-09": [ShareSpan(1, D(100), _at(20))],
        "2026-10": [ShareSpan(1, D(100), _at(20))],
    }
    spend = {"2026-09": D(1070), "2026-10": D(1050)}
    assert attribute_periods(spend, D(1000), shares) == {1: 100}  # 70 + 50, clamped to 100
    assert attribute_periods({"2026-09": D(1030), "2026-10": D(1020)}, D(1000), shares) == {1: 50}


def test_tie_on_expiry_ordered_by_id():
    shares = [ShareSpan(7, D(50), _at(10)), ShareSpan(3, D(50), _at(10))]
    assert attribute(D(1060), D(1000), shares) == {3: 50, 7: 10}
