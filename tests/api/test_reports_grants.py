from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models import Grant, GrantShare


@pytest.fixture
async def report_grants(seeded, snapshot):
    now = datetime.now(UTC)
    async with seeded() as s:
        for group, department, ref, amount, status, shared, used, returned, upn in (
            (1, 1, "PRJ/X", 10000, "active", 4000, 0, 0, "dani@corp.example"),
            (2, 1, "PRJ/X", 20000, "closed", 8000, "3000.1234", "16999.8766", "avi@corp.example"),
            (1, 1, "PRJ-Y", 5000, "pending", 0, 0, 0, "dani@corp.example"),
            (5, 2, "PRJ/X", 7000, "active", 0, 0, 0, "viewer@corp.example"),
        ):
            g = Grant(
                group_id=group,
                department_id=department,
                project_ref=ref,
                amount=amount,
                status=status,
                used=Decimal(str(used)),
                returned=Decimal(str(returned)),
                starts_at=now - timedelta(days=1),
                expires_at=now + timedelta(days=10),
                closed_at=now if status == "closed" else None,
            )
            s.add(g)
            await s.flush()
            if shared:
                s.add(
                    GrantShare(
                        grant_id=g.id,
                        upn=upn,
                        amount=shared,
                        used=Decimal(str(used)),
                        status="closed" if status == "closed" else "active",
                        expires_at=now + timedelta(days=5),
                    )
                )
        await s.commit()
    await snapshot([("employee", "dani@corp.example", None, "16000.4321")])


async def test_by_project_totals_match_project_summary(login, report_grants):
    admin = await login("admin")
    r = await admin.get("/reports/grants")
    assert r.status_code == 200, r.text
    b = r.json()
    rows = {row["project_ref"]: row for row in b["rows"]}
    assert set(rows) == {"PRJ/X", "PRJ-Y"}
    project = (await admin.get("/projects/summary", params={"project_ref": "PRJ/X"})).json()
    x = rows["PRJ/X"]
    assert {k: x[k] for k in project["totals"]} == project["totals"]
    assert (x["departments"], x["groups"], x["grants"]) == (2, 3, 3)
    assert x["unshared"] == 25000 and x["used"] == 4000.5555
    assert x["returned"] == 16999.8766
    assert b["totals"] == {
        "departments": 2,
        "groups": 3,
        "grants": 4,
        "amount": 42000,
        "shared": 12000,
        "unshared": 30000,
        "used": 4000.5555,
        "returned": 16999.8766,
    }
    filtered = await admin.get("/reports/grants", params={"department": 1, "project_ref": "PRJ/X"})
    assert filtered.json()["totals"]["amount"] == 30000
    assert len(filtered.json()["rows"]) == 1
    empty = (await admin.get("/reports/grants?project_ref=missing")).json()
    assert empty["rows"] == [] and all(v == 0 for v in empty["totals"].values())


async def test_pmo_other_department_hidden(login, report_grants):
    pmo = await login("pmo")
    r = await pmo.get("/reports/grants")
    assert r.status_code == 200, r.text
    assert r.json()["totals"]["amount"] == 35000
    assert r.json()["totals"]["departments"] == 1
    assert (await pmo.get("/reports/grants?department=2")).status_code == 404
    assert (await pmo.get("/reports/grants?department=999")).status_code == 404
    assert (await pmo.get("/reports/grants?department=oops")).status_code == 422


async def test_allocation_pool_and_closed_grant_conserve_credit(login, report_grants):
    r = await (await login("rep")).get("/reports/allocation?level=department&id=1")
    assert r.status_code == 200, r.text
    rows = {row["id"]: row for row in r.json()["rows"]}
    pool = rows["pmo_pool"]
    assert pool["total"] == 180000 and pool["allocated"] == 15000
    assert pool["spend"] == 4000.5555
    assert pool["remaining"] == 161999.8766
    assert rows[1]["total"] == 260000
    # The closed grant retains spent credit in the closing month's team ceiling.
    assert rows[2]["total"] == 153000.1234


async def test_head_sees_own_group_only(login, report_grants):
    head = await login("dana")
    r = await head.get("/reports/grants")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["totals"]["amount"] == 15000 and b["totals"]["groups"] == 1
    assert b["totals"]["used"] == 1000.4321
    assert {row["project_ref"] for row in b["rows"]} == {"PRJ/X", "PRJ-Y"}
    viewer = (await (await login("viewer")).get("/reports/grants?department=1")).json()
    assert viewer["totals"]["amount"] == 35000
    assert (await (await login("dani")).get("/reports/grants")).json()["rows"] == []
