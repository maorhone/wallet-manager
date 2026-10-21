from datetime import UTC, datetime, timedelta

from app.models import (
    Department,
    DepartmentModelAllowance,
    Employee,
    Grant,
    GrantShare,
    GroupModelPot,
    RoleBinding,
)

GRANT = {  # an active grant of group A2 (Noa's group)
    "department_id": 1, "group_id": 2, "project_ref": "PRJ-T", "purpose": "t", "amount": 1000,
    "status": "active", "starts_at": datetime.now(UTC) - timedelta(days=1),
    "expires_at": datetime.now(UTC) + timedelta(days=30),
}  # fmt: skip


async def test_split_and_unallocated(login, seeded, snapshot):
    async with seeded() as s:
        (await s.get(Department, 1)).budget = 1200000
        s.add(DepartmentModelAllowance(department_id=1, model="gpt-4", amount=500000))
        s.add(GroupModelPot(group_id=1, model="gpt-4", amount=100000))
        s.add(GroupModelPot(group_id=2, model="gpt-4", amount=50000))
        await s.commit()
    await snapshot([("department", 1, None, 300000), ("group", 1, None, 100000)])
    rep = await login("rep")
    r = await rep.get("/departments/1/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["split"]["pmo_pool"] == 180000 and body["split"]["reserve"] == 50000
    assert body["split"]["unallocated"] == 200000  # 1,200,000 - (770,000 + 180,000 + 50,000)
    assert {g["id"]: g["base"] for g in body["split"]["groups"]} == {
        1: 250000, 2: 150000, 3: 350000, 4: 20000,
    }  # fmt: skip
    assert body["wallet"]["total"] == 1200000 and body["wallet"]["used"] == 300000
    assert body["wallet"]["remaining"] == 900000 and body["wallet"]["unallocated"] == 200000
    a1 = next(g for g in body["groups"] if g["id"] == 1)
    assert (a1["base"], a1["grants"], a1["total"], a1["used"], a1["percent"]) == (
        250000, 0, 250000, 100000, 0.4,
    )  # fmt: skip
    assert a1["state"] == "healthy"
    assert a1["sync_status"] in ("pending", "applied")
    assert body["split"]["pmo_reserve_sync_status"] in ("pending", "applied")
    assert body["allowances"] == [{"model": "gpt-4", "amount": 500000, "pots_sum": 150000}]
    assert body["actions"] == {
        "edit_split": True,
        "edit_budget": False,
        "edit_allowances": False,
        "place": True,
    }
    assert body["period"] and body["reset_date"] and body["read_only"] is False


async def test_over_allocated_state_and_admin_actions(login, seeded):
    async with seeded() as s:
        (await s.get(Department, 1)).budget = 900000
        await s.commit()
    body = (await (await login("admin")).get("/departments/1/summary")).json()
    assert body["wallet"]["state"] == "over_allocated" and body["wallet"]["unallocated"] == -100000
    assert body["actions"] == {
        "edit_split": True,
        "edit_budget": True,
        "edit_allowances": True,
        "place": True,
    }


async def test_group_heads_rows_for_rep(login, seeded, tmp_path):
    async with seeded() as s:
        s.add(Grant(**GRANT))
        await s.commit()
    rep = await login("rep")
    body = (await rep.get("/departments/1/summary")).json()
    heads = {h["upn"]: h for h in body["group_heads"]}
    assert set(heads) == {"dana@corp.example", "noa@corp.example"}
    dana = heads["dana@corp.example"]
    assert dana["group_name"] == "Group A1" and dana["base_cap"] >= 0
    assert dana["actions"] == {"edit": True, "share": False}  # A1 has no active grant
    assert heads["noa@corp.example"]["actions"] == {"edit": True, "share": True}
    for key in ("group_id", "shares", "model_caps", "max_parallel", "rpm_limit", "sync_status"):
        assert key in dana
    # Viewers and PMO see aggregates only: no personal rows at all (decision 7).
    viewer = (await (await login("viewer")).get("/departments/1/summary")).json()
    assert viewer["group_heads"] == []
    # A representative who is also a group head cannot edit their own row (DR9).
    staff_dn = "CN=grp-d1-staff,OU=Groups,DC=corp,DC=example"  # the rep's own group (id 4)
    async with seeded() as s:
        s.add(RoleBinding(ad_group_dn=staff_dn, role="group_head", scope_kind="group", scope_id=4))
        await s.commit()
    both = (await (await login("rep")).get("/departments/1/summary")).json()
    own = next(h for h in both["group_heads"] if h["upn"] == "rep@corp.example")
    assert own["actions"] == {"edit": False, "share": False}
    other = next(h for h in both["group_heads"] if h["upn"] == "dana@corp.example")
    assert other["actions"]["edit"] is True


async def test_group_heads_shares_sum_and_moved_head(login, seeded):
    async with seeded() as s:
        for _ in range(2):
            g = Grant(**{**GRANT, "group_id": 1})
            s.add(g)
            await s.flush()
            s.add(GrantShare(grant_id=g.id, upn="noa@corp.example", amount=100))
        (await s.get(Employee, "noa@corp.example")).group_id = 1  # moved; AD role is kept
        await s.commit()
    body = (await (await login("rep")).get("/departments/1/summary")).json()
    noa = next(h for h in body["group_heads"] if h["upn"] == "noa@corp.example")
    assert noa["group_id"] == 1 and noa["shares"] == 200


async def test_group_heads_past_period_listed_read_only(login, seeded):
    body = (await (await login("rep")).get("/departments/1/summary?period=2020-01")).json()
    assert body["read_only"] is True and body["group_heads"]
    assert all(h["actions"]["edit"] is False for h in body["group_heads"])


async def test_summary_scope(login):
    assert (await (await login("dana")).get("/departments/1/summary")).status_code == 404
    assert (await (await login("rep")).get("/departments/2/summary")).status_code == 404
    assert (await (await login("viewer")).get("/departments/1/summary")).status_code == 200
