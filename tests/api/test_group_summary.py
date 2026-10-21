from periods import CURRENT

from app.models import EmployeeModelCap, GroupModelPot

NO_ACTIONS = {
    "edit_members": False,
    "edit_pots_limits": False,
    "edit_defaults": False,
    "service_keys": {"view": False, "manage": False},
}


async def _prep(seeded, snapshot):
    async with seeded() as s:
        s.add(GroupModelPot(group_id=1, model="gpt-4", amount=100000))
        s.add(EmployeeModelCap(upn="dani@corp.example", model="gpt-4", amount=4000))
        await s.commit()
    await snapshot(
        [
            ("group", 1, None, 60000),
            ("group", 1, "gpt-4", 40000),
            ("department", 1, None, 60000),
            ("employee", "dani@corp.example", None, 14000),
            ("employee", "yael@corp.example", None, 9000),
            ("employee", "dana@corp.example", None, 100),
        ]  # fmt: skip
    )


async def test_pmo_sees_summary_without_members(login, seeded, snapshot):
    await _prep(seeded, snapshot)
    pmo = await login("pmo")
    r = await pmo.get("/groups/1/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["wallet"]["total"] == 250000 and body["wallet"]["used"] == 60000
    assert body["pots"] == [
        {"model": "gpt-4", "amount": 100000, "used": 40000, "members_caps_sum": 4000}
    ]
    assert body["top_consumers"] == [] and body["near_limit_members"] == []  # D11
    assert body["actions"] == NO_ACTIONS
    assert body["grants"] == [] and body["pending_sync_count"] == 0
    assert (await pmo.get("/groups/1/members")).status_code == 403


async def test_viewer_aggregates_only(login, seeded, snapshot):
    await _prep(seeded, snapshot)
    viewer = await login("viewer")
    body = (await viewer.get("/groups/1/summary")).json()
    assert body["wallet"]["remaining"] == 190000
    assert body["top_consumers"] == [] and body["actions"] == {
        **NO_ACTIONS,
        "service_keys": {"view": True, "manage": False},
    }
    assert (await viewer.get("/groups/1/members")).status_code == 403


async def test_head_sees_consumers_and_actions(login, seeded, snapshot):
    await _prep(seeded, snapshot)
    body = (await (await login("dana")).get("/groups/1/summary")).json()
    assert [c["upn"] for c in body["top_consumers"]] == [
        "dani@corp.example", "yael@corp.example", "dana@corp.example",
    ]  # fmt: skip
    assert [m["upn"] for m in body["near_limit_members"]] == ["dani@corp.example"]  # 14000/15000
    assert body["actions"] == {
        "edit_members": True,
        "edit_pots_limits": False,
        "edit_defaults": True,
        "service_keys": {"view": True, "manage": True},
    }
    assert body["period"] == CURRENT and body["read_only"] is False
    assert body["group"]["default_member_cap"] == 0  # prefill for the defaults dialog


async def test_summary_hidden_outside_scope(login):
    assert (await (await login("dana")).get("/groups/2/summary")).status_code == 404
