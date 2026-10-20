from app.models import Department, DepartmentModelAllowance, GroupModelPot


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
    assert body["actions"] == {"edit_split": True, "edit_budget": False, "edit_allowances": False}
    assert body["period"] and body["reset_date"] and body["read_only"] is False


async def test_over_allocated_state_and_admin_actions(login, seeded):
    async with seeded() as s:
        (await s.get(Department, 1)).budget = 900000
        await s.commit()
    body = (await (await login("admin")).get("/departments/1/summary")).json()
    assert body["wallet"]["state"] == "over_allocated" and body["wallet"]["unallocated"] == -100000
    assert body["actions"] == {"edit_split": True, "edit_budget": True, "edit_allowances": True}


async def test_summary_scope(login):
    assert (await (await login("dana")).get("/departments/1/summary")).status_code == 404
    assert (await (await login("rep")).get("/departments/2/summary")).status_code == 404
    assert (await (await login("viewer")).get("/departments/1/summary")).status_code == 200
