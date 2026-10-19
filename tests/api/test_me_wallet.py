from periods import CURRENT

from app.models import Employee, EmployeeModelCap

DANI, DANA = "dani@corp.example", "dana@corp.example"


async def test_fields_fr_e1(login, seeded, snapshot):
    async with seeded() as s:
        s.add(EmployeeModelCap(upn=DANI, model="gpt-4", amount=5000))
        e = await s.get(Employee, DANI)
        e.max_parallel, e.rpm_limit = 4, 120
        await s.commit()
    await snapshot(
        [
            ("employee", DANI, None, 3000),
            ("employee", DANI, "gpt-4", 2000),
            ("employee", DANI, "claude", 1000),
            ("group", 1, None, 50000),
            ("department", 1, None, 200000),
        ]  # fmt: skip
    )
    r = await (await login("dani")).get("/me/wallet")
    assert r.status_code == 200, r.text
    body = r.json()
    w = body["wallet"]
    assert (w["total"], w["base_cap"], w["shares_total"]) == (15000, 15000, 0)
    assert (w["used"], w["remaining"], w["percent"], w["state"]) == (3000, 12000, 0.2, "healthy")
    assert w["effective_remaining"] == 12000
    assert w["binding"] == {"level": "self", "group_percent": 0.2, "department_percent": 0.2}
    models = {m["model"]: m for m in body["models"]}
    assert models["gpt-4"] == {"model": "gpt-4", "cap": 5000, "used": 2000, "remaining": 3000}
    assert models["claude"]["cap"] is None and models["claude"]["used"] == 1000
    assert body["limits"]["max_parallel"] == 4 and body["limits"]["rpm_limit"] == 120
    assert body["shares"] == []
    assert body["group"] == {"id": 1, "name": "Group A1", "percent": 0.2, "state": "healthy"}
    assert body["department"] == {"id": 1, "name": "Department D1", "percent": 0.2}
    assert body["period"] == CURRENT and body["read_only"] is False
    assert body["reset_date"] > f"{CURRENT}-28" and body["spend_taken_at"]
    assert body["sync_status"] in ("applied", "pending", "failed")


async def test_binding_constraint_group_exhausted(login, snapshot):
    await snapshot(
        [("employee", DANI, None, 1000), ("group", 1, None, 250000), ("department", 1, None, 0)]
    )
    body = (await (await login("dani")).get("/me/wallet")).json()
    w = body["wallet"]
    assert w["remaining"] == 14000 and w["state"] == "blocked_group"  # personal budget is left
    assert w["effective_remaining"] == 0
    assert w["binding"]["level"] == "group" and w["binding"]["group_percent"] == 1
    assert body["group"]["state"] == "blocked_own"


async def test_owner_names_from_directory(login):
    owners = (await (await login("dani")).get("/me/wallet")).json()["owners"]
    assert owners["base_cap"]["role"] == "group_head"
    assert [p["display_name"] for p in owners["base_cap"]["people"]] == ["Dana Levi"]
    assert owners["limits"] == owners["model_caps"] == owners["base_cap"]
    head = (await (await login("dana")).get("/me/wallet")).json()["owners"]
    assert head["base_cap"]["role"] == "department_rep"
    assert head["base_cap"]["people"] == [{"upn": "rep@corp.example", "display_name": "Rina Rep"}]


async def test_future_period_is_422(login):
    r = await (await login("dani")).get("/me/wallet?period=2999-01")
    assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"


async def test_usage_series_per_day_and_model(login, seeded):
    from app.models import SpendSnapshot

    async with seeded() as s:
        for day, model, v in (("2026-03-02", "gpt-4", 5), ("2026-03-02", "claude", 1)):
            s.add(
                SpendSnapshot(
                    scope_kind="employee", scope_id=DANI, period=day, model=model, spend=v
                )
            )
        s.add(
            SpendSnapshot(
                scope_kind="employee", scope_id=DANI, period="2026-03-09", model="gpt-4", spend=2
            )
        )
        s.add(
            SpendSnapshot(
                scope_kind="employee", scope_id=DANA, period="2026-03-02", model="gpt-4", spend=99
            )
        )
        await s.commit()
    r = await (await login("dani")).get("/me/usage?from=2026-03-01&to=2026-03-05")
    assert r.json() == [
        {"day": "2026-03-02", "model": "claude", "spend": 1},
        {"day": "2026-03-02", "model": "gpt-4", "spend": 5},
    ]
