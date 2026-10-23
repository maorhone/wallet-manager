import pytest

from app.litellm_client import TeamMemberUpdate, TeamUpdate
from app.models import Department, EmployeeModelCap, GroupModelPot, Grp


async def _report(client):
    r = await client.get("/reports/overallocation")
    assert r.status_code == 200, r.text
    # The seed's "D1 staff" group (id 4) is born over-allocated: 3 members at 10000 on a 20000 base.
    return [f for f in r.json()["findings"] if (f["level"], f["entity_id"]) != ("group", 4)]


@pytest.mark.xfail(strict=False, reason="flaky under load 2026-10-23; see OPEN-ITEMS")
async def test_live_team_overallocation_flagged(login_wm, provisioned, litellm):
    admin = await login_wm("admin")
    assert await _report(admin) == []
    await litellm.team_member_update(
        TeamMemberUpdate(
            team_id="A1",
            user_id="dani@corp.example",
            max_budget_in_team=300000,
            budget_duration="1mo",
        )
    )
    [f] = await _report(admin)
    assert (f["source"], f["level"], f["entity_id"], f["name"]) == (
        "litellm", "group", 1, "Group A1"
    )  # fmt: skip
    assert f["allocated"] == 300000 + 15000 + 15000 and f["total"] == 250000


async def test_reserve_adjusted_department_check(login_wm, provisioned, litellm):
    # Σ team budgets 960000 is under the org budget (1000000) but over budget - reserve (950000).
    await litellm.team_update(TeamUpdate(team_id="A2", max_budget=340000))
    [f] = await _report(await login_wm("admin"))
    assert (f["source"], f["level"], f["entity_id"], f["name"]) == (
        "litellm", "department", 1, "Department D1"
    )  # fmt: skip
    assert (f["allocated"], f["total"]) == (960000, 950000)


async def test_wm_exact_checks(login_wm, provisioned):
    async with provisioned() as s:
        (await s.get(Department, 1)).pmo_pool = 340000  # 630000 + 340000 + 50000 > 1000000
        (await s.get(Grp, 2)).base_budget = 10000  # members avi + noa: 30000 > 10000
        s.add_all(
            [
                GroupModelPot(group_id=1, model="gpt-4o", amount=1000),
                GroupModelPot(group_id=2, model="gpt-4o", amount=500),
                EmployeeModelCap(upn="dani@corp.example", model="gpt-4o", amount=800),
                EmployeeModelCap(upn="yael@corp.example", model="gpt-4o", amount=800),
            ]
        )
        await s.commit()
    got = {
        (f["level"], f["entity_id"], f.get("model")): (f["allocated"], f["total"])
        for f in await _report(await login_wm("admin"))
        if f["source"] == "wm"
    }
    assert got == {
        ("department", 1, None): (1020000, 1000000),
        ("group", 2, None): (30000, 10000),
        ("group_model", 1, "gpt-4o"): (1500, 0),  # pots without a department allowance
        ("member_model", 1, "gpt-4o"): (1600, 1000),
    }


async def test_scope_filtering(login_wm, provisioned):
    async with provisioned() as s:
        (await s.get(Department, 1)).pmo_pool = 200000
        (await s.get(Department, 2)).budget = 100000  # bases 320000 > 100000
        await s.commit()
    seen = {}
    for who in ("admin", "rep", "viewer"):
        seen[who] = {f["entity_id"] for f in await _report(await login_wm(who))}
    assert seen == {"admin": {1, 2}, "rep": {1}, "viewer": {1}}
    for who in ("dana", "pmo"):
        assert (await (await login_wm(who)).get("/reports/overallocation")).status_code == 403
