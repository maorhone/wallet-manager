from decimal import Decimal

from grants import A1, A2, create, grant_body, send, when

from app.models import Grant, GrantShare


async def _make(pmo, group_id, ref, amount=10000, **extra):
    r = await create(pmo, grant_body(group_id, amount, project_ref=ref, **extra))
    assert r.status_code == 200, r.text
    return int(r.json()["entities"][0]["id"])


async def test_list_filters_and_visibility(login, provisioned):
    pmo = await login("pmo")
    g1 = await _make(pmo, A1, "PRJ-1")
    await _make(pmo, A2, "PRJ-2")
    await _make(pmo, A1, "PRJ-3", starts_at=when(days=3), expires_at=when(days=9))

    def ids(r):
        assert r.status_code == 200, r.text
        return {row["project_ref"] for row in r.json()}

    assert ids(await pmo.get("/grants")) == {"PRJ-1", "PRJ-2", "PRJ-3"}
    assert ids(await pmo.get("/grants?group=1")) == {"PRJ-1", "PRJ-3"}
    assert ids(await pmo.get("/grants?status=pending")) == {"PRJ-3"}
    assert ids(await pmo.get("/grants?project_ref=PRJ-2")) == {"PRJ-2"}
    assert ids(await pmo.get("/grants?department=1&group=2")) == {"PRJ-2"}
    # a head sees only their group; a viewer of the department sees all; staff see none
    assert ids(await (await login("dana")).get("/grants")) == {"PRJ-1", "PRJ-3"}
    assert ids(await (await login("viewer")).get("/grants?department=1")) == {
        "PRJ-1", "PRJ-2", "PRJ-3",
    }  # fmt: skip
    assert (await (await login("dani")).get("/grants")).json() == []
    # filter scope: 404 outside every visible scope, as for any other read
    assert (await (await login("dana")).get("/grants?group=1")).status_code == 200
    assert (await (await login("dana")).get("/grants?group=2")).status_code == 404
    assert (await (await login("dani")).get("/grants?group=1")).status_code == 404
    assert (await pmo.get("/grants?department=2")).status_code == 404
    row = next(r for r in (await pmo.get("/grants")).json() if r["id"] == g1)
    assert row["group_name"] == "Group A1" and row["unshared"] == 10000 and row["shared"] == 0
    assert row["actions"] == {"edit": True, "close": True, "share": False}
    head_row = (await (await login("dana")).get("/grants?project_ref=PRJ-1")).json()[0]
    assert head_row["actions"] == {"edit": False, "close": False, "share": True}
    assert 28 <= row["days_left"] <= 30


async def test_pmo_pool_formula(login, provisioned):
    pmo = await login("pmo")
    await _make(pmo, A1, "PRJ-1", 40000)  # active
    await _make(pmo, A1, "PRJ-2", 30000, starts_at=when(days=3))  # pending reserves too
    closed = await _make(pmo, A2, "PRJ-3", 20000)
    async with provisioned() as s:
        s.add(GrantShare(grant_id=closed, upn="avi@corp.example", amount=Decimal(8000),
                         used=Decimal(5000), returned=Decimal(3000), status="closed",
                         closed_reason="manual"))  # fmt: skip
        await s.commit()
    assert (await send(pmo, "POST", f"/grants/{closed}/close", {})).status_code == 200
    body = (await pmo.get("/departments/1/pmo-pool")).json()
    assert body["pool"] == 180000 and body["active_grants"] == 70000
    # 180000 - (40000 + 30000) - 5000 used by the closed grant; 15000 went back
    assert body["available"] == 105000 and body["returned"] == 15000 and body["used"] == 5000
    assert body["actions"] == {"create_grant": True}
    assert (await (await login("rep")).get("/departments/1/pmo-pool")).json()["actions"] == {
        "create_grant": False
    }
    assert (await (await login("dana")).get("/departments/1/pmo-pool")).status_code == 404
    assert (await pmo.get("/departments/1/pmo-pool?period=2999-01")).status_code == 422
    past = (await pmo.get("/departments/1/pmo-pool?period=2020-01")).json()
    assert past["available"] == 110000  # nothing closed in that period


async def test_project_summary_aggregates_visible_grants(login, provisioned):
    pmo = await login("pmo")
    await _make(pmo, A1, "PRJ-X", 10000)
    gid = await _make(pmo, A2, "PRJ-X", 20000)
    async with provisioned() as s:
        s.add(GrantShare(grant_id=gid, upn="avi@corp.example", amount=Decimal(8000),
                         used=Decimal(3000)))  # fmt: skip
        await s.commit()
    body = (await pmo.get("/projects/PRJ-X/summary")).json()
    assert body["project_ref"] == "PRJ-X" and len(body["grants"]) == 2
    assert body["totals"] == {"amount": 30000, "shared": 8000, "used": 3000, "returned": 0}
    head = (await (await login("dana")).get("/projects/PRJ-X/summary")).json()
    assert head["totals"]["amount"] == 10000 and len(head["grants"]) == 1
    assert (await (await login("dani")).get("/projects/PRJ-X/summary")).status_code == 404
    assert (await pmo.get("/projects/NOPE/summary")).status_code == 404
    async with provisioned() as s:
        assert (await s.get(Grant, gid)).amount == 20000


async def test_project_summary_query_form_serves_slash_refs(login, provisioned):
    pmo = await login("pmo")
    await _make(pmo, A1, "FY/2026", 10000)
    assert (await pmo.get("/projects/FY%2F2026/summary")).status_code == 404  # path form cannot
    r = await pmo.get("/projects/summary", params={"project_ref": "FY/2026"})
    assert r.status_code == 200 and r.json()["totals"]["amount"] == 10000
    assert (await pmo.get("/projects/summary", params={"project_ref": "NOPE"})).status_code == 404
    assert (await pmo.get("/projects/summary")).status_code == 422
