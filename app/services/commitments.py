"""The ONE place that computes how much of a group is committed (docs/design/commitments.md).

committed = base-funded consumption + unspent member caps + unspent active initiatives
          + unspent group keys (all generations) + children at max(budget, spent).
``compute`` is a pure function over plain rows; the DB part only loads them. Every check that
can raise a group's commitments goes through ``assert_fits`` (group lock, fresh spend, 503 when
spend is unreadable). Unsettled incoming credit is not part of ``total``: it is subtracted from
the capacity (``settled_budget``).
"""

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db import LockKind
from app.errors import ApiError
from app.models import Employee, Grant, GrantShare, Grp, Initiative, ServiceKey
from app.services.attribution import ShareSpan, attribute
from app.services.spend import Spend, SpendUnavailable, fresh_spend, month_bounds
from app.services.validation import (
    check_sum,
    internal_allocated,
    key_allocated,
    pending_incoming,
    settled_budget,
)

ZERO = Decimal(0)


@dataclass(frozen=True)
class Snapshot:
    consumption_base: Decimal  # the group's own spend funded from its base budget
    consumption_pmo: Decimal  # the part funded by PMO grant shares
    member_caps_unspent: Decimal
    initiatives_unspent: Decimal
    keys_unspent: Decimal
    children: Decimal
    pending_incoming: Decimal = ZERO  # reported, EXCLUDED from total

    @property
    def total(self) -> Decimal:
        return (
            self.consumption_base
            + self.member_caps_unspent
            + self.initiatives_unspent
            + self.keys_unspent
            + self.children
        )


@dataclass(frozen=True)
class MemberRow:
    upn: str
    cap: Decimal
    spend: Decimal
    left: bool = False
    shares: tuple[ShareSpan, ...] = ()  # grant shares overlapping the period (active and closed)


@dataclass(frozen=True)
class KeyRow:
    id: int
    max_budget: Decimal
    retired: Decimal
    used: Decimal


@dataclass(frozen=True)
class InitiativeRow:
    id: int
    budget: Decimal
    used: Decimal


@dataclass(frozen=True)
class ChildRow:
    id: int
    base: Decimal
    spent: Decimal


@dataclass(frozen=True)
class Rows:
    group_spend: Decimal
    members: tuple[MemberRow, ...] = ()
    keys: tuple[KeyRow, ...] = ()  # active/pending group-owned keys
    initiatives: tuple[InitiativeRow, ...] = ()  # active only
    children: tuple[ChildRow, ...] = ()  # non-archived
    pending_incoming: Decimal = ZERO


@dataclass(frozen=True)
class Change:
    """A proposed change, applied to the rows without writing (docs/design/commitments.md §1)."""

    caps: dict[str, Decimal] = field(default_factory=dict)  # upn -> new base cap
    cap_increase: dict[str, Decimal] = field(default_factory=dict)  # upn -> + amount (approval)
    child_budgets: dict[int, Decimal] = field(default_factory=dict)  # child -> new base
    new_children: dict[int, Decimal] = field(default_factory=dict)  # child -> base (move)
    remove_children: frozenset[int] = frozenset()
    new_keys: tuple[Decimal, ...] = ()  # max_budget of each new group key
    rotate_keys: frozenset[int] = frozenset()
    new_initiatives: tuple[Decimal, ...] = ()
    initiative_budgets: dict[int, Decimal] = field(default_factory=dict)
    initiative_increase: dict[int, Decimal] = field(default_factory=dict)
    credit: Decimal = ZERO  # a plain increase (transfer restore)
    base: Decimal | None = None  # a proposed base budget of the group itself


def apply(rows: Rows, change: Change) -> Rows:
    """The rows as they would be after ``change``."""
    members = {m.upn: m for m in rows.members}
    for upn, cap in change.caps.items():
        members[upn] = (
            replace(members[upn], cap=cap) if upn in members else MemberRow(upn, cap, ZERO)
        )
    for upn, amount in change.cap_increase.items():
        members[upn] = replace(members[upn], cap=members[upn].cap + amount)
    children = {c.id: c for c in rows.children}
    for cid, base in {**change.child_budgets, **change.new_children}.items():
        children[cid] = (
            replace(children[cid], base=base) if cid in children else ChildRow(cid, base, ZERO)
        )
    for cid in change.remove_children:
        children.pop(cid, None)
    keys = {k.id: k for k in rows.keys}
    for kid in change.rotate_keys:
        keys[kid] = replace(keys[kid], retired=keys[kid].retired + keys[kid].max_budget)
    new_keys = tuple(KeyRow(-i - 1, b, ZERO, ZERO) for i, b in enumerate(change.new_keys))
    ini = {i.id: i for i in rows.initiatives}
    for iid, budget in change.initiative_budgets.items():
        ini[iid] = replace(ini[iid], budget=budget)
    for iid, amount in change.initiative_increase.items():
        ini[iid] = replace(ini[iid], budget=ini[iid].budget + amount)
    new_ini = tuple(InitiativeRow(-i - 1, b, ZERO) for i, b in enumerate(change.new_initiatives))
    return replace(
        rows,
        members=tuple(members.values()),
        keys=(*keys.values(), *new_keys),
        initiatives=(*ini.values(), *new_ini),
        children=tuple(children.values()),
    )


def compute(rows: Rows, with_shares: bool = False) -> Snapshot:
    """Pure arithmetic. ``with_shares`` adds active grant shares to each allocation (display)."""
    pmo = ZERO
    caps = ZERO
    for m in rows.members:
        pmo += sum(attribute(m.spend, m.cap, m.shares).values(), ZERO)
        if not m.left:
            extra = ZERO
            if with_shares:
                extra = sum((s.amount for s in m.shares if s.used is None), ZERO)
            caps += max(m.cap + extra - m.spend, ZERO)
    return Snapshot(
        consumption_base=max(rows.group_spend - pmo, ZERO),
        consumption_pmo=pmo,
        member_caps_unspent=caps,
        initiatives_unspent=sum((max(i.budget - i.used, ZERO) for i in rows.initiatives), ZERO),
        keys_unspent=sum((max(k.max_budget + k.retired - k.used, ZERO) for k in rows.keys), ZERO),
        children=sum((max(c.base, c.spent) for c in rows.children), ZERO),
        pending_incoming=rows.pending_incoming,
    )


async def load_rows(db, group: Grp, spend: Spend, tz: str) -> Rows:
    from app.services.service_keys import initiative_used

    start, end = month_bounds(spend.period, ZoneInfo(tz))
    shares: dict[str, list[ShareSpan]] = {}
    for s, g in await db.execute(
        select(GrantShare, Grant)
        .join(Grant, Grant.id == GrantShare.grant_id)
        .where(Grant.group_id == group.id)
    ):
        expires = s.expires_at or g.expires_at
        if g.starts_at < end and expires > start:
            used = s.used if s.status == "closed" else None
            shares.setdefault(s.upn, []).append(ShareSpan(s.id, s.amount, expires, used))
    members = tuple(
        MemberRow(
            e.upn, e.base_cap, spend.used("employee", e.upn), e.status == "left",
            tuple(shares.get(e.upn, ())),
        )
        for e in await db.scalars(select(Employee).where(Employee.group_id == group.id))
    )  # fmt: skip
    keys = tuple(
        KeyRow(k.id, k.max_budget or ZERO, k.retired_budget, spend.used("service_key", k.id))
        for k in await db.scalars(
            select(ServiceKey).where(
                ServiceKey.status.in_(("active", "pending")),
                ServiceKey.owner_kind == "group",
                ServiceKey.owner_id == group.id,
            )
        )
    )
    # An initiative's consumption comes from ALL its keys, revoked and moved ones included.
    initiatives = []
    for i in await db.scalars(
        select(Initiative).where(Initiative.group_id == group.id, Initiative.status == "active")
    ):
        used = await initiative_used(db, spend, i.id, spend.period, tz, window=True)
        initiatives.append(InitiativeRow(i.id, i.budget, used))
    children = tuple(
        ChildRow(c.id, c.base_budget, spend.used("group", c.id))
        for c in await db.scalars(
            select(Grp).where(Grp.parent_group_id == group.id, Grp.status != "archived")
        )
    )
    return Rows(
        spend.used("group", group.id), members, keys, tuple(initiatives), children,
        await pending_incoming(db, "group", group.id),
    )  # fmt: skip


async def snapshot(
    db, group: Grp, spend: Spend, *, tz: str, change: Change | None = None, with_shares=False
) -> Snapshot:
    rows = await load_rows(db, group, spend, tz)
    return compute(apply(rows, change) if change else rows, with_shares)


def _unavailable() -> ApiError:
    return ApiError(503, "SPEND_UNAVAILABLE", "Current usage could not be read; try again shortly")


async def read_spend(db, reader, clock, tz: str) -> Spend:
    """Fresh spend, never zero when unreadable (503)."""
    zone = ZoneInfo(tz)
    try:
        now = (clock.now() if clock else datetime.now(zone)).astimezone(zone)
        return await fresh_spend(db, reader, now)
    except SpendUnavailable:
        raise _unavailable() from None


async def _is_noop(db, group: Grp, c: Change) -> bool:
    """True when the change leaves every stored figure as it is (r18 decision 2)."""
    if (
        c.new_keys
        or c.rotate_keys
        or c.new_initiatives
        or c.new_children
        or c.credit
        or any(c.cap_increase.values())
        or any(c.initiative_increase.values())
        or c.initiative_budgets
    ):
        return False  # fmt: skip
    if c.base is not None and c.base != group.base_budget:
        return False
    q = select(Employee.upn, Employee.base_cap).where(Employee.upn.in_(c.caps))
    caps = dict((await db.execute(q)).all())
    if caps != c.caps:
        return False
    q = select(Grp.id, Grp.base_budget).where(Grp.id.in_(c.child_budgets))
    kids = dict((await db.execute(q)).all())
    return kids == c.child_budgets and not c.remove_children


async def check(db, spend: Spend, group: Grp, change: Change, tz: str, rows=None) -> None:
    """The bound itself, with spend already read. Raises the over-allocation error."""
    snap = await snapshot(db, group, spend, tz=tz, change=change)
    base = group.base_budget if change.base is None else change.base
    if err := check_sum(
        snap.total + change.credit,
        await settled_budget(db, group, base),
        group.overalloc_factor,
        rows,
    ):
        raise err


async def assert_fits(ctx, reader, clock, group: Grp, change: Change, rows=None) -> None:
    """Group lock, fresh spend (503 when unreadable), then the one bound check."""
    if change.remove_children and not (change.caps or change.new_keys or change.credit):
        return  # a removal only lowers the commitments
    if await _is_noop(ctx.db, group, change):
        return
    await ctx.lock(LockKind.GROUP, group.id)
    await ctx.db.refresh(group)  # a transfer may have changed the budget before the lock
    tz = ctx.settings.app_timezone
    spend = await read_spend(ctx.db, reader, clock, tz)
    await check(ctx.db, spend, group, change, tz, rows)


async def assert_fits_initiative(db, initiative: Initiative, extra: Decimal, rotate=False) -> None:
    """A key on an initiative: every credential generation reserves its cap inside the budget."""
    from app.services.service_keys import initiative_committed

    budget = initiative.budget - await pending_incoming(db, "initiative", initiative.id)
    if err := check_sum(await initiative_committed(db, initiative.id) + extra, budget):
        raise err


async def assert_fits_system(db, system, extra: Decimal) -> None:
    if err := check_sum(await key_allocated(db, "system", system.id) + extra, system.budget):
        raise err


async def check_group_tree(
    ctx, reader, clock, dept, groups, bases: dict, pmo: Decimal, reserve: Decimal,
    rows=None, moved: int | None = None,
) -> None:  # fmt: skip
    """Top-level bases + pools fit the department; each parent of a changed child fits.

    ``moved`` is a group that just got a new parent: it counts as a new child there.
    """
    live = [g for g in groups if g.status != "archived"]
    top = sum((bases[g.id] for g in live if g.parent_group_id is None), ZERO)
    internal = await internal_allocated(ctx.db, dept.id)
    if err := check_sum(top + pmo + reserve + internal, dept.budget, rows=rows):
        raise err
    for parent in live:
        kids = {g.id: bases[g.id] for g in live if g.parent_group_id == parent.id}
        changed = {g.id: bases[g.id] for g in live if g.id in kids and bases[g.id] != g.base_budget}
        cut = bases[parent.id] < parent.base_budget
        if not (changed or cut or moved in kids):
            continue
        change = Change(
            child_budgets=changed,
            new_children={moved: kids[moved]} if moved in kids else {},
            base=bases[parent.id],
        )
        await assert_fits(ctx, reader, clock, parent, change, rows)
