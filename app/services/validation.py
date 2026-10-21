"""Pure allocation rules (FR-D2, FR-D3, §11.2). Callers raise the returned error."""

from decimal import Decimal

from app.errors import ApiError, limit_out_of_policy, parent_overallocated


def check_sum(
    children_total: Decimal, parent_total: Decimal, factor: Decimal | int = 1, rows=None
) -> ApiError | None:
    available = parent_total * factor
    if children_total > available:
        return parent_overallocated(children_total, available, parent_total, rows)
    return None


def check_model_sums(
    new_map_by_child: dict, parent_map: dict[str, Decimal], factor: Decimal | int = 1
) -> ApiError | None:
    """Per model, Σ children > parent (a missing parent counts as 0)."""
    totals: dict[str, Decimal] = {}
    for amounts in new_map_by_child.values():
        for model, amount in amounts.items():
            totals[model] = totals.get(model, Decimal(0)) + amount
    for model, total in sorted(totals.items()):
        err = check_sum(total, parent_map.get(model, Decimal(0)), factor)
        if err:
            err.details = {"model": model}
            return err
    return None


def check_limit(field: str, value, policy: dict, is_admin: bool) -> ApiError | None:
    """D7: an admin may exceed ``max`` but never go below ``min``."""
    if value >= policy["min"] and (is_admin or value <= policy["max"]):
        return None
    return limit_out_of_policy(field, value, policy["min"], policy["max"])


async def key_allocated(db, owner_kind: str, owner_id: int) -> Decimal:
    """Pending keys reserve budget while synchronous generation is in flight."""
    from sqlalchemy import func, select

    from app.models import ServiceKey

    return await db.scalar(
        select(func.coalesce(func.sum(ServiceKey.max_budget), 0)).where(
            ServiceKey.owner_kind == owner_kind,
            ServiceKey.owner_id == owner_id,
            ServiceKey.status.in_(("active", "pending")),
        )
    )


async def group_allocated(db, group) -> Decimal:
    from sqlalchemy import func, select

    from app.models import Employee

    members = await db.scalar(
        select(func.coalesce(func.sum(Employee.base_cap), 0)).where(
            Employee.group_id == group.id,
            Employee.status != "left",
        )
    )
    return members + await key_allocated(db, "group", group.id)
