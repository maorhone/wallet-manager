from sqlalchemy import select

from app.models import AuditLog, Department, Grp
from app.outbox.worker import drain

A1, A2, C1, STAFF = 1, 2, 5, 4  # group ids in seed order (seed/dev.yaml)


async def test_split_within_budget_pending_then_team_max_budget_applied(
    login, split, provisioned, litellm, prov_settings
):
    client = await login("rep")
    r = await split(client, [{"group_id": A1, "base": 200000}, {"group_id": A2, "base": 160000}])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending"
    ents = {(e["entity"], e["id"]): e for e in body["entities"]}
    assert ents[("group", str(A1))]["sync_status"] == "pending"
    assert ents[("group", str(A1))]["values"]["base_budget"] == 200000
    assert ("department", "1") in ents  # pmo_pool / reserve row

    await drain(provisioned, litellm, prov_settings)
    assert (await litellm.team_info("A1")).team_info.max_budget == 200000
    assert (await litellm.team_info("A2")).team_info.max_budget == 160000
    assert (await litellm.team_info("B1")).team_info.max_budget == 350000  # unlisted: unchanged


async def test_split_over_budget_422_with_numbers(login, split, provisioned):
    client = await login("rep")
    r = await split(client, [{"group_id": A1, "base": 270000}])
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "PARENT_OVERALLOCATED"
    assert (body["requested"], body["available"], body["parent_total"]) == (
        1020000, 1000000, 1000000,
    )  # fmt: skip
    assert body["details"]["rows"] == [{"group_id": A1, "code": "PARENT_OVERALLOCATED"}]
    async with provisioned() as s:
        assert (await s.get(Grp, A1)).base_budget == 250000
        assert await s.scalar(select(AuditLog.id).where(AuditLog.reason == "split")) is None


async def test_split_pmo_and_reserve_count_toward_sum(login, split, provisioned):
    r = await split(await login("rep"), [], pmo_pool=200000)
    assert r.status_code == 422 and r.json()["requested"] == 1020000


async def test_split_foreign_group_scope_forbidden(login, split, provisioned):
    r = await split(await login("rep"), [{"group_id": C1, "base": 1}])
    assert r.status_code == 422 and r.json()["code"] == "SCOPE_FORBIDDEN"


async def test_staff_group_is_regular_row(login, split, provisioned):
    r = await split(
        await login("rep"), [{"group_id": A1, "base": 240000}, {"group_id": STAFF, "base": 30000}]
    )
    assert r.status_code == 200, r.text
    assert {e["id"] for e in r.json()["entities"] if e["entity"] == "group"} == {
        str(A1), str(STAFF),
    }  # fmt: skip
    async with provisioned() as s:
        assert (await s.get(Grp, STAFF)).base_budget == 30000
        rows = (
            await s.scalars(
                select(AuditLog).where(AuditLog.entity == "group", AuditLog.reason == "split")
            )
        ).all()
        assert sorted(r.entity_id for r in rows) == [str(A1), str(STAFF)]
        assert {r.before["base_budget"] for r in rows} == {250000, 20000}
        dept = await s.get(Department, 1)
        assert (dept.pmo_pool, dept.reserve) == (180000, 50000)


async def test_split_unchanged_group_not_audited_and_pmo_reserve_audited(login, split, provisioned):
    r = await split(
        await login("rep"), [{"group_id": A1, "base": 250000}], pmo_pool=190000, reserve=40000
    )
    assert r.status_code == 200, r.text
    assert [e["entity"] for e in r.json()["entities"]] == ["department"]
    async with provisioned() as s:
        dept = await s.get(Department, 1)
        assert (dept.pmo_pool, dept.reserve) == (190000, 40000)


async def test_pmo_rep_viewer_permissions(login, split, provisioned):
    groups = [{"group_id": A1, "base": 240000}]
    assert (await split(await login("admin"), groups)).status_code == 200
    assert (await split(await login("rep"), groups)).status_code == 200
    for who in ("pmo", "viewer", "dana"):
        r = await split(await login(who), groups)
        assert r.status_code in (403, 404), who
    assert (await split(await login("pmo"), groups)).json()["code"] == "FORBIDDEN"
    assert (await split(await login("dana"), groups)).json()["code"] == "NOT_FOUND"


async def test_split_missing_reason_and_unknown_dept(login, split, provisioned):
    client = await login("admin")
    assert (await split(client, [], dept_id=999)).status_code == 404
    r = await client.put(
        "/departments/1/split",
        json={"groups": [], "pmo_pool": 0, "reserve": 0},
        headers={"X-CSRF-Token": client.cookies["wm_csrf"]},
    )
    assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"
