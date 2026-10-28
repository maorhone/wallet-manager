"""redesign U1: public services against the brute-force oracle (tests/oracle.py), ~40 examples.

Hypothesis only draws the scenarios (deterministic); they run in one async test, each one on a
cleaned database, through the real HTTP API. An accepted operation must satisfy the bound, a
rejected one must really violate it or a cut floor (no false rejects).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import delete, select, text, update

from app.models import Employee, Grant, GrantShare, Grp, Initiative, Transfer
from tests import oracle as o
from tests.api.test_nested_group_budget import child_of
from tests.api.test_transfers import send
from tests.helpers.commit import caps

A1 = 1
MEMBERS = ["dana@corp.example", "dani@corp.example", "yael@corp.example"]
SPEND_USERS = [*MEMBERS, "gone@corp.example"]
money = st.integers(0, 120000)


@st.composite
def scenarios(draw):
    return {
        "spend": [draw(st.sampled_from([0, 0, 1000, 20000, 90000])) for _ in SPEND_USERS],
        "pmo": draw(st.sampled_from([0, 0, 30000, 90000])),  # active share of dani
        "initiative": draw(st.sampled_from([None, 0, 5000, 60000])),
        "transfer": draw(
            st.sampled_from(
                [
                    None,
                    None,
                    ("pending", 30000),
                    ("failed", 60000),
                    ("applied", 50000),
                    ("compensated", 70000),
                ]
            )
        ),
        "child": draw(st.sampled_from([None, 0, 20000, 100000])),
        "op": draw(
            st.sampled_from(["caps", "caps", "initiative", "child"]),
        ),
        "value": draw(money),
        "who": draw(st.sampled_from([1, 2])),  # dani or yael
    }


def drawn(n=40):
    out = []

    @settings(
        max_examples=n, derandomize=True, deadline=None, database=None,
        suppress_health_check=list(HealthCheck),
    )  # fmt: skip
    @given(scenarios())
    def collect(s):
        out.append(s)

    collect()
    return out


async def _reset(sf, fake_engine):
    async with fake_engine.begin() as conn:
        await conn.execute(text('DELETE FROM "LiteLLM_SpendLogs"'))
    async with sf() as s:
        await s.execute(delete(Transfer))
        await s.execute(delete(GrantShare))
        await s.execute(delete(Grant))
        await s.execute(delete(Initiative))
        await s.execute(delete(Grp).where(Grp.parent_group_id == A1))
        await s.execute(update(Employee).where(Employee.group_id == A1).values(base_cap=15000))
        await s.commit()


async def _arrange(sf, fake_engine, sc):
    now = datetime.now(UTC)
    async with fake_engine.begin() as conn:
        for i, (user, amount) in enumerate(zip(SPEND_USERS, sc["spend"], strict=True)):
            if amount:
                await conn.execute(
                    text(
                        'INSERT INTO "LiteLLM_SpendLogs" (request_id, "user", team_id,'
                        ' model_group, api_key, "startTime", "endTime", spend)'
                        " VALUES (:i, :u, 'A1', 'gpt-4', NULL, :at, :at, :s)"
                    ),
                    {"i": f"prop-{i}", "u": user, "at": now, "s": amount},
                )
    async with sf() as s:
        if sc["pmo"]:
            grant = Grant(
                department_id=1, group_id=A1, amount=sc["pmo"], status="active",
                starts_at=now - timedelta(days=1), expires_at=now + timedelta(days=20),
            )  # fmt: skip
            s.add(grant)
            await s.flush()
            s.add(
                GrantShare(
                    grant_id=grant.id,
                    upn=MEMBERS[1],
                    amount=sc["pmo"],
                    status="active",
                    expires_at=now + timedelta(days=20),
                )  # fmt: skip
            )
        if sc["transfer"]:
            state, amount = sc["transfer"]
            s.add(
                Transfer(
                    group_id=A1,
                    source_kind="group",
                    source_id="2",
                    target_kind="group",
                    target_id=str(A1),
                    amount=amount,
                    created_by="prop",
                    state=state,
                )  # fmt: skip
            )
        if sc["initiative"] is not None:
            s.add(
                Initiative(
                    name="P",
                    owner_upn=MEMBERS[2],
                    group_id=A1,
                    department_id=1,
                    budget=sc["initiative"],
                    created_by=MEMBERS[0],
                )  # fmt: skip
            )
        await s.commit()
    if sc["child"] is not None:
        return await child_of(sf, A1, sc["child"])


def model(sc, child_id) -> o.OGroup:
    """The same org as plain rows for the oracle (never reads the database)."""
    members = []
    for i in range(len(MEMBERS)):
        shares = [o.OShare(1, Decimal(sc["pmo"]), 1)] if i == 1 and sc["pmo"] else []
        members.append(o.OMember(Decimal(15000), Decimal(sc["spend"][i]), shares=shares))
    g = o.OGroup(Decimal(250000), extra_spend=Decimal(sc["spend"][3]), members=members)
    if sc["transfer"]:  # raw transfer rows: the oracle derives the unsettled credit itself
        g.transfers.append(o.OTransfer(Decimal(sc["transfer"][1]), sc["transfer"][0]))
    if sc["initiative"] is not None:
        g.initiatives.append(o.OInitiative(Decimal(sc["initiative"])))
    if sc["child"] is not None:
        g.children.append(o.OChild(Decimal(sc["child"]), o.ZERO))
    return g


async def test_services_agree_with_the_oracle(login_wm, seeded, fake_engine):
    head, rep = await login_wm("dana"), await login_wm("rep")
    h = {"X-CSRF-Token": rep.cookies["wm_csrf"], "X-Change-Reason": "prop"}
    accepted = rejected = 0
    for sc in drawn():
        await _reset(seeded, fake_engine)
        child_id = await _arrange(seeded, fake_engine, sc)
        before = model(sc, child_id)
        value, who = Decimal(sc["value"]), sc["who"]
        floor_ok = True
        if sc["op"] == "caps":
            after = o.with_cap(before, who, value)
            floor_ok = value >= 15000 or value >= before.members[who].spend
            noop = value == 15000
            r = await head.put(
                f"/groups/{A1}/members/caps",
                json=caps(**{MEMBERS[who].split("@")[0]: sc["value"]}),
                headers={"X-CSRF-Token": head.cookies["wm_csrf"], "X-Change-Reason": "prop"},
            )
        elif sc["op"] == "initiative":
            after, noop = o.with_initiative(before, value), False
            r = await send(
                head, "POST", f"/groups/{A1}/initiatives",
                {"name": "New", "owner_upn": MEMBERS[2], "budget": sc["value"]},
            )  # fmt: skip
        else:
            if child_id is None:
                continue
            after = o.with_child_base(before, 0, value)
            noop = value == sc["child"]
            floor_ok = True  # the child has no members and no spend of its own
            body = {
                "groups": [{"group_id": child_id, "base": sc["value"]}],
                "pmo_pool": 180000, "reserve": 50000,
            }  # fmt: skip
            r = await rep.put("/departments/1/split", json=body, headers=h)
        expected_ok = noop or (floor_ok and o.fits(after))
        assert (r.status_code == 200) == expected_ok, (sc, r.status_code, r.text)
        if r.status_code != 200:
            rejected += 1
            code = "PARENT_OVERALLOCATED" if floor_ok else "INVALID_REQUEST"
            assert r.json()["code"] == code, (sc, r.text)
        else:
            accepted += 1
    assert accepted and rejected  # the scenarios must exercise both outcomes
    async with seeded() as s:
        assert await s.scalar(select(Employee.base_cap).where(Employee.upn == MEMBERS[2])) == 15000
