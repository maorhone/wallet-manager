"""redesign U1: the pure commitment arithmetic against the brute-force oracle (tests/oracle.py)."""

from datetime import UTC, datetime
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.services import commitments as c
from app.services.attribution import ShareSpan
from tests import oracle as o

D = Decimal
money = st.integers(0, 400).map(Decimal)


@st.composite
def shares(draw):
    n = draw(st.integers(0, 3))
    return [
        o.OShare(
            i + 1,
            draw(money),
            draw(st.integers(1, 5)),
            closed := draw(st.booleans()),
            draw(money) if closed else o.ZERO,
        )  # fmt: skip
        for i in range(n)
    ]


@st.composite
def groups(draw):
    mem = [
        o.OMember(
            draw(money), draw(money), draw(st.sampled_from([False, False, True])), draw(shares())
        )
        for _ in range(draw(st.integers(0, 4)))
    ]
    keys = [
        o.OKey(draw(money), draw(money), draw(money), draw(st.booleans()))
        for _ in range(draw(st.integers(0, 3)))
    ]
    initiatives = [
        o.OInitiative(
            draw(money), [draw(money) for _ in range(draw(st.integers(0, 3)))], draw(st.booleans())
        )
        for _ in range(draw(st.integers(0, 2)))
    ]
    children = [
        o.OChild(draw(money), draw(money), draw(st.sampled_from([False, False, True])))
        for _ in range(draw(st.integers(0, 3)))
    ]
    states = ["pending", "failed", "applied", "RESERVED", "SETTLED", "COMPENSATING", "STUCK"]
    transfers = [
        o.OTransfer(
            draw(money), draw(st.sampled_from(states)), draw(st.booleans()),
            draw(st.booleans()), draw(st.booleans()),
        )
        for _ in range(draw(st.integers(0, 3)))
    ]  # fmt: skip
    return o.OGroup(
        draw(money), draw(st.integers(1, 2)), draw(money), mem, keys, initiatives, children,
        transfers,
    )  # fmt: skip


def to_rows(g: o.OGroup) -> c.Rows:
    day = lambda n: datetime(2026, 1, n, tzinfo=UTC)  # noqa: E731
    return c.Rows(
        group_spend=g.spend,
        members=tuple(
            c.MemberRow(
                f"m{i}", m.cap, m.spend, m.left,
                tuple(ShareSpan(s.id, s.amount, day(s.expires), s.used if s.closed else None)
                      for s in m.shares),
            )
            for i, m in enumerate(g.members)
        ),
        keys=tuple(
            c.KeyRow(i, k.max, k.retired, k.spend) for i, k in enumerate(g.keys) if k.active
        ),
        initiatives=tuple(
            c.InitiativeRow(i, x.budget, sum(x.key_spends, o.ZERO))
            for i, x in enumerate(g.initiatives)
            if x.active
        ),
        children=tuple(
            c.ChildRow(i, k.base, k.spend) for i, k in enumerate(g.children) if not k.archived
        ),
        pending_incoming=g.pending_incoming,
    )  # fmt: skip


PROPS = settings(
    max_examples=300, derandomize=True, deadline=None, suppress_health_check=list(HealthCheck)
)


@PROPS
@given(groups())
def test_snapshot_total_equals_oracle(g):
    snap = c.compute(to_rows(g))
    assert snap.total == o.committed(g)
    assert snap.pending_incoming == g.pending_incoming  # reported, never part of the total
    assert snap.consumption_base >= 0 and snap.consumption_pmo >= 0


@PROPS
@given(groups(), st.data())
def test_applied_change_equals_oracle_on_a_copy(g, data):
    rows = to_rows(g)
    live = list(range(len(g.members)))
    cases = []
    if live:
        i, value = data.draw(st.sampled_from(live)), data.draw(money)
        cases.append((c.Change(caps={f"m{i}": value}), o.with_cap(g, i, value)))
        cases.append((c.Change(cap_increase={f"m{i}": value}), o.with_cap_increase(g, i, value)))
    value = data.draw(money)
    cases.append((c.Change(new_keys=(value,)), o.with_key(g, value)))
    cases.append((c.Change(new_initiatives=(value,)), o.with_initiative(g, value)))
    active_keys = [i for i, k in enumerate(g.keys) if k.active]
    if active_keys:
        i = data.draw(st.sampled_from(active_keys))
        cases.append((c.Change(rotate_keys=frozenset({i})), o.with_rotation(g, i)))
    kids = [i for i, k in enumerate(g.children) if not k.archived]
    if kids:
        i = data.draw(st.sampled_from(kids))
        cases.append((c.Change(child_budgets={i: value}), o.with_child_base(g, i, value)))
    for change, expected in cases:
        assert c.compute(c.apply(rows, change)).total == o.committed(expected), change


def test_pmo_funded_spend_is_not_base():
    # base cap 10, PMO share 100, spend 100: 10 of it is base, 90 PMO (r18 B1c)
    g = o.OGroup(
        Decimal(100),
        members=[o.OMember(Decimal(10), Decimal(100), shares=[o.OShare(1, Decimal(100), 1)])],
    )
    snap = c.compute(to_rows(g))
    assert (snap.consumption_base, snap.consumption_pmo) == (10, 90)
    assert snap.total == 10 and o.fits(g)


# redesign U1-fix1 oracle-ledger: key and initiative consumption is group consumption
def test_oracle_counts_key_and_initiative_consumption_even_when_revoked():
    key = o.OKey(D(100), D(0), D(60))
    g = o.OGroup(D(500), keys=[key])
    assert o.committed(g) == 100 and c.compute(to_rows(g)).total == 100
    key.active = False  # revoked: its 60 stays consumed
    assert o.committed(g) == 60 and c.compute(to_rows(g)).total == 60
    g = o.OGroup(D(500), initiatives=[o.OInitiative(D(100), [D(30), D(20)], active=False)])
    assert o.committed(g) == 50 and c.compute(to_rows(g)).total == 50


def test_capacity_is_derived_from_raw_transfer_rows():
    t = [
        o.OTransfer(D(10), "RESERVED"),
        o.OTransfer(D(20), "COMPENSATING"),  # not reverted: still unavailable
        o.OTransfer(D(40), "COMPENSATING", reverted=True),  # credit left the domain
        o.OTransfer(D(5), "SETTLED"),
        o.OTransfer(D(7), "STUCK", incoming=False, reverted=True),  # held source restore
        o.OTransfer(D(3), "STUCK", incoming=False, reverted=True, restore_confirmed=True),
    ]
    g = o.OGroup(D(100), factor=2, transfers=t)
    assert (g.pending_incoming, g.held_restore) == (30, 7)
    assert o.capacity(g) == (100 - 30 - 7) * 2
