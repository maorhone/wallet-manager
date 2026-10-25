"""Step 5.17: section budget overview (4 KPIs, bar, rows) and the Action Center groups."""

import uuid

from app.models import Grant, Request

A1 = 1  # dana heads it; dani and yael are members with a base cap of 15000 each


async def test_kpis_bar_and_rows(login):
    r = await (await login("dana")).get(f"/budget/overview?scope=group:{A1}")
    assert r.status_code == 200, r.text
    body = r.json()
    k = body["kpis"]
    assert k["total"] == 250000 and k["metered_value"] == 0  # no tokens consumed, nothing missing
    assert body["bar"]["total"] == 250000
    assert {"dani@corp.example", "yael@corp.example"} <= {x["id"] for x in body["rows"]}


async def test_bad_scope_422(login):
    r = await (await login("dana")).get("/budget/overview?scope=nope")
    assert r.status_code == 422


async def test_attention_groups(login, seeded):
    async with seeded() as s:
        s.add(
            Request(
                type="credits", scope_kind="group", scope_id=A1, requester_upn="dani@corp.example",
                handler_role="group_head", handler_scope=A1, status="submitted", amount=500,
            )
        )  # fmt: skip
        s.add(Grant(department_id=1, group_id=A1, amount=1000, status="active", created_by="x"))
        await s.commit()
    r = await (await login("dana")).get(f"/attention?scope=group:{A1}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [x["type"] for x in body["requests"]] == ["credits"]
    assert body["reallocation_opportunities"] and body["shortage_risks"] == []
    assert uuid  # keep import used for later idempotent writes


async def test_department_overview_keeps_org_systems_apart(login, seeded):
    from app.models import ProductionSystem

    async with seeded() as s:
        s.add(
            ProductionSystem(
                funding="organization", organization_id=1, responsible_department_id=1,
                name="OrgSys", budget=1000, status="active",
            )
        )  # fmt: skip
        await s.commit()
    r = await (await login("rep")).get("/budget/overview?scope=department:1")
    assert r.status_code == 200, r.text
    body = r.json()
    summary = (await (await login("rep")).get("/departments/1/summary")).json()
    assert body["kpis"]["total"] == summary["wallet"]["total"]
    assert body["kpis"]["used"] == summary["wallet"]["used"]
    assert [x["name"] for x in body["org_systems"]] == ["OrgSys"]
    assert body["internal_systems"] == [] or all(
        x["name"] != "OrgSys" for x in body["internal_systems"]
    )
    assert body["sections"] and body["tree"] and body["previous_period"]["period"]
    assert body["actions"]["edit_hierarchy"] is True


async def test_department_overview_hidden_from_plain_head(login):
    r = await (await login("dana")).get("/budget/overview?scope=department:1")
    assert r.status_code == 404  # no department role: existence is hidden
