"""Pure wallet formulas (SPEC §12.3) and states (§12.4). No I/O."""

from decimal import Decimal

ZERO = Decimal(0)


def percent(used: Decimal, total: Decimal) -> Decimal:
    """Used / total as a fraction; a spent wallet with no total counts as full."""
    if total > 0:
        return used / total
    return Decimal(1) if used > 0 else ZERO


def _card(total, allocated, used, remaining=None) -> dict:
    return {
        "total": total, "allocated": allocated, "unallocated": total - allocated,
        "used": used, "remaining": total - used if remaining is None else remaining,
        "percent": percent(used, total),
    }  # fmt: skip


def department_wallet(budget, group_bases, pmo_pool, reserve, used) -> dict:
    return _card(budget, group_bases + pmo_pool + reserve, used)


def pmo_wallet(pool, active_grants, closed_used, used) -> dict:
    card = _card(pool, active_grants, used)
    card["unallocated"] = card["remaining"] = pool - active_grants - closed_used
    return card


def group_wallet(base, grants, member_caps, shares, used) -> dict:
    return _card(base + grants, member_caps + shares, used)


def employee_wallet(base_cap, shares, used) -> dict:
    return _card(base_cap + shares, ZERO, used)


def effective_remaining(employee, group, department) -> Decimal:
    return min(employee, group, department)


def binding_level(employee, group, department) -> str:
    """The level that blocks first (FR-E2); ties go to the narrower level."""
    smallest = min(employee, group, department)
    if employee == smallest:
        return "self"
    return "group" if group == smallest else "department"


def state(
    total,
    used,
    own_remaining,
    parent_remainings: list[tuple[str, Decimal]],
    allocated=None,
    near: Decimal = Decimal("0.8"),
    full: Decimal = Decimal(1),
    status: str = "active",
) -> str:
    """blocked_own > blocked_parent > over_allocated > near_limit > healthy; left/blocked: muted."""
    if status in ("left", "blocked"):
        return "muted"
    used_pct = percent(used, total)
    if used_pct >= full:
        return "blocked_own"
    if own_remaining > 0:
        for level, remaining in parent_remainings:
            if remaining <= 0:
                return f"blocked_{level}"
    if allocated is not None and allocated > total:
        return "over_allocated"
    return "near_limit" if used_pct >= near else "healthy"
