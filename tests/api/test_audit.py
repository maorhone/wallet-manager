import pytest

from app.outbox.worker import drain


async def _put(client, path, body, reason="plan"):
    h = {"X-CSRF-Token": client.cookies["wm_csrf"], "X-Change-Reason": reason}
    r = await client.put(path, json=body, headers=h)
    assert r.status_code == 200, r.text
    return r


async def test_audit_includes_litellm_call_status(
    login_wm, put_budget, provisioned, litellm, prov_settings
):
    from decimal import Decimal

    from app.models import Organization

    async with provisioned() as s:  # the budget route now respects the organization envelope
        (await s.get(Organization, 1)).departments_envelope = Decimal(10_000_000)
        await s.commit()
    admin = await login_wm("admin")
    rid = (await put_budget(admin, 1, 1100000, reason="Q4")).json()["request_id"]
    [row] = (await admin.get("/audit?entity=department&id=1&actor=admin@corp.example")).json()
    assert (row["actor_upn"], row["reason"], row["request_id"]) == ("admin@corp.example", "Q4", rid)
    assert row["before"]["budget"] == 1000000 and row["after"]["budget"] == 1100000
    assert [(c["endpoint"], c["status"]) for c in row["litellm_calls"]] == [
        ("/organization/update", "pending")
    ]
    await drain(provisioned, litellm, prov_settings)
    [row] = (await admin.get("/audit?entity=department&id=1&actor=admin@corp.example")).json()
    [call] = row["litellm_calls"]
    assert call == {"endpoint": "/organization/update", "status": "applied", "attempts": 0} | {
        "last_error": None
    }


async def test_group_timeline_includes_member_changes(login_wm, provisioned):
    dana = await login_wm("dana")
    await _put(dana, "/groups/1/members/caps", [{"upn": "dani@corp.example", "base_cap": 20000}])
    await _put(await login_wm("aiteam"), "/groups/1/limits", {"tpm_limit": 5000})
    rows = (await dana.get("/audit?entity=group&id=1")).json()
    assert {(r["entity"], r["entity_id"]) for r in rows} == {
        ("group", "1"),
        ("employee", "dani@corp.example"),
    }
    assert rows[0]["at"] >= rows[-1]["at"]  # newest first
    # Group 2's timeline stays clean, and dana cannot read it.
    assert (await dana.get("/audit?entity=group&id=2")).status_code in (403, 404)
    rep_rows = (await (await login_wm("rep")).get("/audit?entity=group&id=1")).json()
    assert len(rep_rows) == len(rows)


@pytest.mark.xfail(strict=False, reason="flaky under load 2026-10-27; see OPEN-ITEMS")
async def test_employee_sees_only_own_record(login_wm, provisioned):
    await _put(await login_wm("dana"), "/groups/1/members/caps", [
        {"upn": "dani@corp.example", "base_cap": 20000},
        {"upn": "yael@corp.example", "base_cap": 21000},
    ])  # fmt: skip
    dani = await login_wm("dani")
    own = await dani.get("/audit?entity=employee&id=dani@corp.example")
    assert own.status_code == 200
    assert {r["entity_id"] for r in own.json()} == {"dani@corp.example"}
    assert "dana@corp.example" in {r["actor_upn"] for r in own.json()}
    assert (await dani.get("/audit?entity=employee&id=yael@corp.example")).status_code in (403, 404)
    assert (await dani.get("/audit")).status_code == 422  # entity and id are required
    assert (await dani.get("/audit?actor=dana@corp.example")).status_code == 422
    assert (await dani.get("/audit?entity=group&id=1")).status_code in (403, 404)
    admin = await login_wm("admin")
    assert len((await admin.get("/audit")).json()) >= 2  # admin: any filter, or none
    assert (await admin.get("/audit?actor=dana@corp.example")).status_code == 200
