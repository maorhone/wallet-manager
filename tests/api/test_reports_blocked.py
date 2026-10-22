from datetime import UTC, datetime, timedelta

from app.models import EmployeeModelCap, Grant, GrantShare

DANI = "dani@corp.example"


async def blocked(client, **params):
    return await client.get(
        "/reports/blocked",
        params={
            "scope": "group",
            "id": "1",
            "from": "2026-06-02",
            "to": "2026-06-04",
            **params,
        },
    )


async def test_employee_at_base_cap_listed_per_day_with_day_spend(login, seeded, snapshot):
    # FR-R5 explicitly names base cap; active shares must not hide base-cap exhaustion.
    now = datetime.now(UTC)
    async with seeded() as db:
        grant = Grant(
            department_id=1,
            group_id=1,
            amount=1000,
            status="active",
            starts_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=10),
        )
        db.add(grant)
        await db.flush()
        db.add(GrantShare(grant_id=grant.id, upn=DANI, amount=1000, expires_at=grant.expires_at))
        await db.commit()
    await snapshot([("employee", DANI, None, "14999.9999")], period="2026-06-01")
    await snapshot([("employee", DANI, None, ".0001")], period="2026-06-02")
    await snapshot([("employee", DANI, None, "2.1234")], period="2026-06-04")
    # Previous month must not leak into this month's running spend.
    await snapshot([("employee", DANI, None, 99999)], period="2026-05-31")
    r = await blocked(await login("dana"))
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    assert [r["day"] for r in rows] == ["2026-06-02", "2026-06-03", "2026-06-04"]
    assert [r["day_spend"] for r in rows] == [0.0001, 0, 2.1234]
    assert [r["period_spend_to_date"] for r in rows] == [15000, 15000, 15002.1234]
    assert all(
        r["cap"] == 15000
        and r["kind"] == "base"
        and r["model"] is None
        and r["upn"] == DANI
        and r["group_id"] == 1
        for r in rows
    )
    assert (await blocked(await login("viewer"))).status_code == 403
    assert (await blocked(await login("pmo"))).status_code == 403
    assert (await blocked(await login("dani"), scope="employee", id=DANI)).status_code == 200


async def test_model_cap_reached_listed(login, seeded, snapshot):
    async with seeded() as db:
        db.add(EmployeeModelCap(upn=DANI, model="m", amount=5))
        await db.commit()
    await snapshot([("employee", DANI, "m", 5), ("employee", DANI, None, 5)], period="2026-06-02")
    rows = (await blocked(await login("dana"), to="2026-06-02")).json()["rows"]
    assert rows == [
        dict(
            day="2026-06-02",
            upn=DANI,
            display_name="Dani Cohen",
            group_id=1,
            kind="model",
            model="m",
            cap=5,
            period_spend_to_date=5,
            day_spend=5,
        )
    ]


async def test_below_cap_not_listed(login, snapshot):
    await snapshot([("employee", DANI, None, "14999.9999")], period="2026-06-02")
    assert (await blocked(await login("dana"))).json() == {"rows": []}


async def test_month_reset_current_caps_and_no_future_projection(login, seeded, snapshot):
    from app.clock import FakeClock
    from app.models import Employee

    async with seeded() as db:
        member = await db.get(Employee, DANI)
        member.base_cap = 10
        await db.commit()
    await snapshot([("employee", DANI, None, 10)], period="2026-06-30")
    await snapshot([("employee", DANI, None, 9)], period="2026-07-01")
    await snapshot([("employee", DANI, None, 1)], period="2026-07-02")
    client = await login("dana")
    client._transport.app.state.clock = FakeClock(datetime(2026, 7, 2, 12, tzinfo=UTC))
    rows = (await blocked(client, **{"from": "2026-06-30", "to": "2026-07-03"})).json()["rows"]
    assert [r["day"] for r in rows] == ["2026-06-30", "2026-07-02"]
    assert all(r["cap"] == r["period_spend_to_date"] == 10 for r in rows)
