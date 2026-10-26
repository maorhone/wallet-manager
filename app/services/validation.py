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


async def internal_allocated(db, department_id: int, exclude_id: int | None = None) -> Decimal:
    """D-076: active internal systems are carved from the department envelope."""
    from sqlalchemy import func, select

    from app.models import ProductionSystem

    return await db.scalar(
        select(func.coalesce(func.sum(ProductionSystem.budget), 0)).where(
            ProductionSystem.department_id == department_id,
            ProductionSystem.funding == "department",
            ProductionSystem.status == "active",
            ProductionSystem.id != (exclude_id or 0),
        )
    )


async def pending_incoming(db, kind: str, ident) -> Decimal:
    """The ONE definition of unsettled credit (D-069, review-p5-r3).

    A transfer's target budget grows at once, but the credit is not spendable, allocatable or
    key-reservable until the transfer is ``applied`` (source reduction applied, fence passed).
    Available balance = budget - commitments - this; every allocation check counts it as committed.

    ONE RULE (review-p5-r5): unsettled credit is excluded from the BASE before any over-allocation
    factor is applied: capacity = (budget - pending_incoming) * factor (``settled_budget``), and a
    reduction may never go below committed funding + pending_incoming. Never add it to allocations.
    """
    from sqlalchemy import func, select

    from app.models import Transfer

    return await db.scalar(
        select(func.coalesce(func.sum(Transfer.amount), 0)).where(
            Transfer.target_kind == kind,
            Transfer.target_id == str(ident),
            Transfer.state.in_(("pending", "failed")),
        )
    )


async def key_allocated(db, owner_kind: str, owner_id: int) -> Decimal:
    """Pending keys reserve budget while synchronous generation is in flight.

    A system also keeps every retired generation reserved and its unsettled incoming credit.
    """
    from sqlalchemy import func, select

    from app.models import ServiceKey

    if owner_kind == "system":
        # Complete system commitment: every key ever issued (revoked/failed too, an upstream
        # delete may be pending or a timeout may hide a created key) plus retired generations.
        return await db.scalar(
            select(
                func.coalesce(
                    func.sum(func.coalesce(ServiceKey.max_budget, 0) + ServiceKey.retired_budget), 0
                )
            ).where(ServiceKey.owner_kind == "system", ServiceKey.owner_id == owner_id)
        ) + await pending_incoming(db, "system", owner_id)
    return await db.scalar(
        select(func.coalesce(func.sum(ServiceKey.max_budget), 0)).where(
            ServiceKey.owner_kind == owner_kind,
            ServiceKey.owner_id == owner_id,
            ServiceKey.status.in_(("active", "pending")),
        )
    )


async def group_own_allocated(db, group) -> Decimal:
    """Member caps plus service-key budgets (what a group carves for itself)."""
    from sqlalchemy import func, select

    from app.models import Employee

    members = await db.scalar(
        select(func.coalesce(func.sum(Employee.base_cap), 0)).where(
            Employee.group_id == group.id,
            Employee.status != "left",
        )
    )
    initiatives = await initiative_allocated(db, group.id)
    return members + initiatives + await key_allocated(db, "group", group.id)


async def settled_budget(db, group, base: Decimal | None = None) -> Decimal:
    """Group base minus unsettled incoming credit: the only part the factor may multiply."""
    return (group.base_budget if base is None else base) - await pending_incoming(
        db, "group", group.id
    )


async def initiative_allocated(db, group_id: int, exclude_id: int | None = None) -> Decimal:
    """5.6: an active initiative's budget is carved from its section's allocatable."""
    from sqlalchemy import func, select

    from app.models import Initiative

    return await db.scalar(
        select(func.coalesce(func.sum(Initiative.budget), 0)).where(
            Initiative.group_id == group_id,
            Initiative.status == "active",
            Initiative.id != (exclude_id or 0),
        )
    )


async def group_allocated(db, group) -> Decimal:
    """D-055: a child group's base is carved from its parent, so it counts here once."""
    from sqlalchemy import func, select

    from app.models import Grp

    children = await db.scalar(
        select(func.coalesce(func.sum(Grp.base_budget), 0)).where(
            Grp.parent_group_id == group.id, Grp.status != "archived"
        )
    )
    return children + await group_own_allocated(db, group)


async def check_group_tree(
    db, dept, groups, bases: dict, pmo: Decimal, reserve: Decimal, rows=None
) -> ApiError | None:
    """Top-level bases + pools fit the department; each parent covers children + own caps."""
    live = [g for g in groups if g.status != "archived"]
    top = sum((bases[g.id] for g in live if g.parent_group_id is None), Decimal(0))
    internal = await internal_allocated(db, dept.id)
    if err := check_sum(top + pmo + reserve + internal, dept.budget, rows=rows):
        return err
    for parent in live:
        kids = sum((bases[g.id] for g in live if g.parent_group_id == parent.id), Decimal(0))
        if kids and (
            err := check_sum(
                kids + await group_own_allocated(db, parent),
                await settled_budget(db, parent, bases[parent.id]),
                parent.overalloc_factor,
                rows,
            )
        ):
            return err
    return None
