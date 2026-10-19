"""SPEC §12.4 state rules."""

from decimal import Decimal as D

from app.services.wallet import state


def _s(total, used, parents=(), allocated=None, status="active"):
    return state(D(total), D(used), D(total) - D(used), list(parents), allocated, status=status)


def test_healthy():
    assert _s(100, 79) == "healthy"


def test_near_limit_at_80_percent():
    assert _s(100, 80) == "near_limit"
    assert _s(100, 99) == "near_limit"


def test_blocked_own_at_100_percent():
    assert _s(100, 100) == "blocked_own"
    assert _s(100, 130) == "blocked_own"


def test_blocked_by_group_when_personal_left():
    assert _s(100, 10, [("group", D(0)), ("department", D(500))]) == "blocked_group"


def test_blocked_by_department_when_personal_left():
    assert _s(100, 10, [("group", D(50)), ("department", D(-1))]) == "blocked_department"


def test_own_beats_parent():
    assert _s(100, 100, [("group", D(0))]) == "blocked_own"


def test_parent_beats_near():
    assert _s(100, 90, [("group", D(0))]) == "blocked_group"


def test_over_allocated_level():
    assert _s(100, 10, allocated=D(120)) == "over_allocated"
    assert _s(100, 10, allocated=D(100)) == "healthy"
    assert _s(100, 100, allocated=D(120)) == "blocked_own"  # blocked outranks the warning


def test_left_and_blocked_are_muted():
    assert _s(100, 100, status="left") == "muted"
    assert _s(100, 0, status="blocked") == "muted"


def test_custom_thresholds():
    assert state(D(100), D(60), D(40), [], None, near=D("0.5")) == "near_limit"
