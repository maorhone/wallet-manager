from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from periods import CURRENT, PREVIOUS

from app.clock import FakeClock
from app.models import (
    AuditLog,
    DepartmentModelAllowance,
    EmployeeModelCap,
    Grant,
    GrantShare,
    GroupModelPot,
)

DANI = "dani@corp.example"


async def report(client, level, id_, **params):
    return await client.get("/reports/allocation", params={"level": level, "id": id_, **params})


async def test_department_rows_groups_pmo_reserve_and_models(login, seeded, snapshot):
    async with seeded() as s:
        s.add(DepartmentModelAllowance(department_id=1, model="gpt-4", amount=500000))
        await s.commit()
    await snapshot(
        [
            ("department", 1, None, "300000.1234"),
            ("department", 1, "gpt-4", "200000.1234"),
            ("department", 1, "other", 100000),
            ("group", 1, None, "100000.1234"),
        ]
    )
    rep = await login("rep")
    r = await report(rep, "department", 1)
    assert r.status_code == 200, r.text
    b = r.json()
    assert (b["level"], b["id"], b["name"], b["period"]) == (
        "department",
        1,
        "Department D1",
        CURRENT,
    )
    assert b["total"] == b["allocated"] == 1000000
    assert Decimal(str(b["spend"])) == Decimal("300000.1234")
    assert Decimal(str(b["remaining"])) == Decimal("699999.8766")
    assert b["pct"] == pytest.approx(0.3000001234) and b["state"] == "healthy"
    assert b["spend_taken_at"]
    rows = {row["id"]: row for row in b["rows"]}
    assert set(rows) == {1, 2, 3, 4, "pmo_pool", "reserve"}
    assert rows[1]["base"] == 250000 and rows[1]["spend"] == 100000.1234
    assert rows["pmo_pool"]["total"] == 180000 and rows["reserve"]["total"] == 50000
    models = {m["model"]: m for m in b["models"]}
    assert models["gpt-4"]["allocation"] == 500000
    assert models["gpt-4"]["spend"] == 200000.1234
    assert models["gpt-4"]["pct"] == pytest.approx(0.4000002468)
    assert models["other"] == {
        "model": "other",
        "allocation": None,
        "spend": 100000,
        "pct": None,
    }
    assert all("upn" not in row for row in b["rows"])


async def test_group_rows_members_with_shares_and_model_caps(login, seeded, snapshot):
    now = datetime.now(UTC)
    async with seeded() as s:
        g = Grant(
            department_id=1,
            group_id=1,
            project_ref="PRJ-R",
            amount=1000,
            model="gpt-4",
            status="active",
            starts_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=10),
        )
        s.add(g)
        await s.flush()
        s.add(GrantShare(grant_id=g.id, upn=DANI, amount=400, expires_at=now + timedelta(days=5)))
        s.add(GroupModelPot(group_id=1, model="gpt-4", amount=10000))
        s.add(EmployeeModelCap(upn=DANI, model="gpt-4", amount=5000))
        await s.commit()
    await snapshot(
        [
            ("group", 1, None, 15150),
            ("group", 1, "gpt-4", 15150),
            ("employee", DANI, None, 15150),
            ("employee", DANI, "gpt-4", 2000),
        ]
    )
    head = await login("dana")
    r = await report(head, "group", 1)
    assert r.status_code == 200, r.text
    b = r.json()
    summary = (await head.get("/groups/1/summary")).json()
    assert b["total"] == summary["wallet"]["total"] == 251000
    assert b["allocated"] == summary["wallet"]["allocated"] == 45400
    assert b["remaining"] == 235850
    assert b["models"] == [
        {
            "model": "gpt-4",
            "allocation": 11000,
            "spend": 15150,
            "pct": pytest.approx(15150 / 11000),
        }
    ]
    rows = {row["id"]: row for row in b["rows"]}
    assert set(rows) == {DANI, "dana@corp.example", "yael@corp.example"}
    dani = rows[DANI]
    assert (dani["base_cap"], dani["shares_total"], dani["total"], dani["spend"]) == (
        15000,
        400,
        15400,
        15150,
    )
    assert dani["model_caps"] == {"gpt-4": 5000}
    assert dani["models"] == [{"model": "gpt-4", "allocation": 5000, "spend": 2000, "pct": 0.4}]


async def test_employee_level_self_and_head_only(login, snapshot):
    await snapshot([("employee", DANI, None, 1000), ("group", 1, None, 250000)])
    for who in ("dani", "dana", "rep", "admin"):
        r = await report(await login(who), "employee", DANI)
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["id"] == DANI and b["name"] == "Dani Cohen" and b["rows"] == []
        assert (b["total"], b["allocated"], b["spend"], b["remaining"]) == (15000, 0, 1000, 14000)
        assert b["state"] == "blocked_group"
    for who, status in (("yael", 404), ("noa", 403), ("pmo", 403), ("viewer", 403)):
        assert (await report(await login(who), "employee", DANI)).status_code == status
    assert (
        await report(await login("admin"), "employee", "missing@corp.example")
    ).status_code == 404


async def test_viewer_group_level_403_department_ok(login):
    viewer = await login("viewer")
    assert (await report(viewer, "group", 1)).status_code == 403
    assert (await report(viewer, "department", 1)).status_code == 200
    pmo = await login("pmo")
    assert (await report(pmo, "department", 1)).status_code == 403
    assert (await report(pmo, "group", 1)).status_code == 403
    rep = await login("rep")
    assert (await report(rep, "department", 2)).status_code == 404
    for level in ("department", "group"):
        assert (await report(await login("admin"), level, 999)).status_code == 404


async def test_past_period_uses_that_period_snapshot(login, seeded, snapshot):
    async with seeded() as s:
        for entity, id_, before, after in (
            ("department", "1", {"budget": 900000}, {"budget": 1000000}),
            ("group", "1", {"base_budget": 200000}, {"base_budget": 250000}),
            (
                "employee",
                DANI,
                {"base_cap": 9000, "model_caps": {"gpt-4": 3000}},
                {"base_cap": 15000, "model_caps": {"gpt-4": 5000}},
            ),
        ):
            s.add(
                AuditLog(
                    at=datetime.now(UTC), entity=entity, entity_id=id_, before=before, after=after
                )
            )
        s.add(EmployeeModelCap(upn=DANI, model="gpt-4", amount=5000))
        await s.commit()
    await snapshot(
        [("department", 1, None, 999), ("group", 1, None, 999), ("employee", DANI, None, 999)]
    )
    await snapshot(
        [
            ("department", 1, None, 3000),
            ("group", 1, None, 2000),
            ("employee", DANI, None, 1000),
            ("employee", DANI, "gpt-4", 500),
        ],
        period=PREVIOUS,
    )
    rep = await login("rep")
    for level, id_, total, used in (
        ("department", 1, 900000, 3000),
        ("group", 1, 200000, 2000),
        ("employee", DANI, 9000, 1000),
    ):
        r = await report(rep, level, id_, period=PREVIOUS)
        assert r.status_code == 200, r.text
        b = r.json()
        assert (b["period"], b["total"], b["spend"]) == (PREVIOUS, total, used)
        assert b["remaining"] == total - used
        if level == "employee":
            assert b["models"] == [
                {"model": "gpt-4", "allocation": 3000, "spend": 500, "pct": pytest.approx(1 / 6)}
            ]


async def test_future_period_422(login):
    r = await report(await login("admin"), "department", 1, period="2999-01")
    assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"


async def test_default_period_uses_injected_clock_and_local_month(login):
    admin = await login("admin")
    # Jerusalem is already in May at this UTC instant.
    admin._transport.app.state.clock = FakeClock(datetime(2040, 4, 30, 22, tzinfo=UTC))
    r = await report(admin, "department", 1)
    assert r.status_code == 200, r.text
    assert r.json()["period"] == "2040-05"
    assert r.json()["spend"] == 0 and r.json()["spend_taken_at"] is None
    future = await report(admin, "department", 1, period="2040-06")
    assert future.status_code == 422 and future.json()["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"level": "other", "id": "1"},
        {"level": "group", "id": "oops"},
        {"level": "department", "id": "1", "period": "2026-1"},
        {"level": "department", "id": "1", "period": "2026-13"},
    ],
)
async def test_invalid_parameters_422(login, params):
    r = await (await login("admin")).get("/reports/allocation", params=params)
    assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"
