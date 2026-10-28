"""Brute-force reference for the commitment bound (docs/design/commitments.md §6).

Works on plain raw ledger-like rows and never imports ``app.services.commitments``: the whole
org is a tree of dataclasses, the PMO split is an explicit loop, and an operation is applied to a
deep copy before the group is recomputed from scratch.
"""

import copy
from dataclasses import dataclass, field
from decimal import Decimal

ZERO = Decimal(0)


@dataclass
class OShare:
    id: int
    amount: Decimal
    expires: int  # ordering only
    closed: bool = False
    used: Decimal = ZERO  # stored usage of a closed share


@dataclass
class OMember:
    cap: Decimal
    spend: Decimal
    left: bool = False
    shares: list[OShare] = field(default_factory=list)


@dataclass
class OKey:
    max: Decimal
    retired: Decimal
    spend: Decimal
    active: bool = True  # False: revoked/deleted, only its spend remains


@dataclass
class OInitiative:
    budget: Decimal
    key_spends: list[Decimal] = field(default_factory=list)  # revoked keys included
    active: bool = True


@dataclass
class OChild:
    base: Decimal
    spend: Decimal
    archived: bool = False


@dataclass
class OGroup:
    base: Decimal
    factor: int = 1
    extra_spend: Decimal = ZERO  # spend by departed members, revoked keys: in no allocation
    members: list[OMember] = field(default_factory=list)
    keys: list[OKey] = field(default_factory=list)
    initiatives: list[OInitiative] = field(default_factory=list)
    children: list[OChild] = field(default_factory=list)
    pending_incoming: Decimal = ZERO
    held_restore: Decimal = ZERO

    @property
    def spend(self) -> Decimal:
        return self.extra_spend + sum((m.spend for m in self.members), ZERO)


def pmo_of(m: OMember) -> Decimal:
    """An independent loop: closed shares keep their stored usage, active ones take the rest."""
    left, total = max(m.spend - m.cap, ZERO), ZERO
    for s in sorted(m.shares, key=lambda s: (s.expires, s.id)):
        take = s.used if s.closed else min(left, s.amount)
        total += take
        left = max(left - take, ZERO)
    return total


def committed(g: OGroup) -> Decimal:
    consumption = max(g.spend - sum((pmo_of(m) for m in g.members), ZERO), ZERO)
    unspent = ZERO
    for m in g.members:
        if not m.left:
            unspent += max(m.cap - m.spend, ZERO)
    for k in g.keys:
        if k.active:
            unspent += max(k.max + k.retired - k.spend, ZERO)
    for i in g.initiatives:
        if i.active:
            unspent += max(i.budget - sum(i.key_spends, ZERO), ZERO)
    for c in g.children:
        if not c.archived:
            unspent += max(c.base, c.spend)
    return consumption + unspent


def capacity(g: OGroup, base: Decimal | None = None) -> Decimal:
    return ((g.base if base is None else base) - g.pending_incoming - g.held_restore) * g.factor


def fits(g: OGroup, base: Decimal | None = None) -> bool:
    return committed(g) <= capacity(g, base)


def with_cap(g: OGroup, index: int, cap: Decimal) -> OGroup:
    out = copy.deepcopy(g)
    out.members[index].cap = cap
    return out


def with_cap_increase(g: OGroup, index: int, amount: Decimal) -> OGroup:
    return with_cap(g, index, g.members[index].cap + amount)


def with_key(g: OGroup, max_budget: Decimal) -> OGroup:
    out = copy.deepcopy(g)
    out.keys.append(OKey(max_budget, ZERO, ZERO))
    return out


def with_rotation(g: OGroup, index: int) -> OGroup:
    out = copy.deepcopy(g)
    out.keys[index].retired += out.keys[index].max
    return out


def with_initiative(g: OGroup, budget: Decimal) -> OGroup:
    out = copy.deepcopy(g)
    out.initiatives.append(OInitiative(budget))
    return out


def with_child_base(g: OGroup, index: int, base: Decimal) -> OGroup:
    out = copy.deepcopy(g)
    out.children[index].base = base
    return out
