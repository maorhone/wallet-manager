from datetime import UTC, datetime, timedelta

from periods import CURRENT, PREVIOUS

from app.models import AuditLog

DANI, YAEL = "dani@corp.example", "yael@corp.example"


def _by_upn(body):
    return {m["upn"]: m for m in body["members"]}


async def test_head_row_not_editable_names_rep(login, snapshot):
    await snapshot([("employee", DANI, None, 3000), ("group", 1, None, 3000)])
    r = await (await login("dana")).get("/groups/1/members")
    assert r.status_code == 200, r.text
    rows = _by_upn(r.json())
    assert set(rows) == {"dana@corp.example", DANI, YAEL}
    head, dani = rows["dana@corp.example"], rows[DANI]
    assert head["is_group_head"] is True and head["editable"] is False
    assert head["owner"]["role"] == "department_rep"
    assert [p["display_name"] for p in head["owner"]["people"]] == ["Rina Rep"]
    assert dani["editable"] is True and dani["owner"]["role"] == "group_head"
    assert (dani["base_cap"], dani["total"], dani["used"], dani["percent"]) == (
        15000, 15000, 3000, 0.2,
    )  # fmt: skip
    assert dani["state"] == "healthy" and dani["status"] == "active"
    assert dani["sync_status"] in ("applied", "pending", "failed")
    assert {"display_name", "shares_total", "model_caps", "max_parallel", "rpm_limit"} <= set(dani)
    assert r.json()["read_only"] is False


async def test_past_period_uses_audit_values_and_read_only(login, seeded, snapshot):
    now = datetime.now(UTC)
    async with seeded() as s:
        s.add_all(
            [
                # dani: 9000 during the previous period, raised afterwards (earliest later `before`)
                AuditLog(
                    at=now, entity="employee", entity_id=DANI, before={"base_cap": 9000},
                    after={"base_cap": 15000},
                ),
                # yael: set to 8000 within the previous period (latest `after` at or before)
                AuditLog(
                    at=now - timedelta(days=40), entity="employee", entity_id=YAEL,
                    before={"base_cap": 1}, after={"base_cap": 8000},
                ),
                AuditLog(
                    at=now, entity="employee", entity_id=YAEL, before={"base_cap": 8000},
                    after={"base_cap": 15000},
                ),
            ]
        )  # fmt: skip
        await s.commit()
    await snapshot([("employee", DANI, None, 4000)], period=PREVIOUS)
    r = await (await login("dana")).get(f"/groups/1/members?period={PREVIOUS}")
    body = r.json()
    rows = _by_upn(body)
    assert body["read_only"] is True and body["period"] == PREVIOUS
    assert rows[DANI]["base_cap"] == 9000 and rows[DANI]["used"] == 4000
    assert rows[YAEL]["base_cap"] == 8000
    assert rows["dana@corp.example"]["base_cap"] == 15000  # no audit rows: current value
    assert not any(m["editable"] for m in rows.values())
    current = _by_upn(
        (await (await login("dana")).get(f"/groups/1/members?period={CURRENT}")).json()
    )
    assert current[DANI]["base_cap"] == 15000
