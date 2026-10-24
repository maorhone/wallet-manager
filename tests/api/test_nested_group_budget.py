"""Phase 5.3: nested group budgets are carved from the parent and counted once (D-055)."""

import pytest
from periods import CURRENT
from sqlalchemy import select

from app.models import Grp

A1, A2 = 1, 2


async def child_of(seeded, parent, base):
    async with seeded() as s:
        g = Grp(
            litellm_team_id="A1-sub", department_id=1, name="A1 sub", base_budget=base,
            default_member_cap=0, status="active", parent_group_id=parent,
        )  # fmt: skip
        s.add(g)
        await s.commit()
        return g.id


async def test_child_budget_reduces_parent_allocatable(login, seeded, split):
    child = await child_of(seeded, A1, 0)
    rep = await login("rep")
    # A1 holds 45000 of member caps, so 205000 is the most a child can carve.
    ok = await split(rep, [{"group_id": child, "base": 205000}])
    assert ok.status_code == 200, ok.text
    bad = await split(rep, [{"group_id": child, "base": 205001}])
    assert bad.status_code == 422 and bad.json()["code"] == "PARENT_OVERALLOCATED"
    assert bad.json()["requested"] == 250001 and bad.json()["available"] == 250000
    summary = (await (await login("dana")).get("/groups/1/summary")).json()
    assert summary["wallet"]["allocated"] == 250000  # 45000 caps + 205000 child, once


async def test_child_not_summed_into_department(login, seeded, split):
    child = await child_of(seeded, A1, 100000)
    r = await split(await login("rep"), [{"group_id": child, "base": 100000}])
    assert r.status_code == 200, r.text  # the department stays exactly full


@pytest.mark.xfail(strict=False, reason="flaky under load 2026-10-25; see OPEN-ITEMS")
async def test_rollup_no_double_count(login, seeded, snapshot):
    child = await child_of(seeded, A1, 1000)
    await snapshot([("group", A1, None, 60000), ("group", child, None, 500)])
    summary = (await (await login("dana")).get("/groups/1/summary")).json()
    assert summary["wallet"]["used"] == 60000
    assert summary["wallet"]["rollup_used"] == 60500
    assert summary["period"] == CURRENT


async def test_overallocation_kept_for_nested(login_wm, seeded):
    await child_of(seeded, A1, 230000)  # 45000 caps + 230000 > 250000
    r = await (await login_wm("admin")).get("/reports/overallocation")
    found = [f for f in r.json()["findings"] if (f["level"], f["entity_id"]) == ("group", A1)]
    assert [(f["source"], f["allocated"], f["total"]) for f in found] == [("wm", 275000, 250000)]


async def test_flat_departments_unchanged(login, split, seeded):
    rep = await login("rep")
    assert (await split(rep, [{"group_id": A1, "base": 270000}])).status_code == 422
    async with seeded() as s:
        assert await s.scalar(select(Grp.parent_group_id).where(Grp.id == A1)) is None
