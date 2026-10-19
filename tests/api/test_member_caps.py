import asyncio

from sqlalchemy import select

from app.models import Employee, Grp
from app.outbox.worker import drain

A1 = 1  # base 250000; members dana (head), dani, yael at 15000 each


def caps(**by_upn):
    return [{"upn": f"{u}@corp.example", "base_cap": c} for u, c in by_upn.items()]


async def _caps(provisioned):
    async with provisioned() as s:
        rows = await s.scalars(select(Employee).where(Employee.group_id == A1))
        return {e.upn.split("@")[0]: e.base_cap for e in rows}


async def test_caps_within_base_applied_to_member_budget(
    login, put, provisioned, litellm, prov_settings
):
    r = await put(await login("dana"), f"/groups/{A1}/members/caps", caps(dani=20000, yael=30000))
    assert r.status_code == 200, r.text
    assert {e["id"] for e in r.json()["entities"]} == {"dani@corp.example", "yael@corp.example"}
    assert r.json()["status"] == "pending"
    await drain(provisioned, litellm, prov_settings)
    members = {m.user_id: m for m in (await litellm.team_info("A1")).team_memberships}
    assert members["dani@corp.example"].litellm_budget_table.max_budget == 20000
    assert members["yael@corp.example"].litellm_budget_table.max_budget == 30000
    assert (await _caps(provisioned))["dani"] == 20000


async def test_caps_over_base_422_with_numbers(login, put, provisioned):
    r = await put(await login("dana"), f"/groups/{A1}/members/caps", caps(dani=250000))
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "PARENT_OVERALLOCATED"
    assert (body["requested"], body["available"], body["parent_total"]) == (280000, 250000, 250000)
    assert body["details"]["rows"] == [{"upn": "dani@corp.example", "code": "PARENT_OVERALLOCATED"}]


async def test_overalloc_factor_allows_overbooking(login, put, provisioned):
    async with provisioned() as s:
        (await s.get(Grp, A1)).overalloc_factor = 1.2
        await s.commit()
    dana = await login("dana")
    assert (await put(dana, f"/groups/{A1}/members/caps", caps(dani=250000))).status_code == 200
    r = await put(dana, f"/groups/{A1}/members/caps", caps(yael=40000))  # 305000 > 300000
    assert r.status_code == 422 and r.json()["available"] == 300000


async def test_group_head_own_row_self_allocation_forbidden(login, put, provisioned):
    r = await put(await login("dana"), f"/groups/{A1}/members/caps", caps(dana=100))
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "SELF_ALLOCATION_FORBIDDEN"
    assert body["details"]["owner_role"] == "department_rep"
    # admins are not exempt (DR9): admin belongs to the D1 staff group (id 4)
    r = await put(await login("admin"), "/groups/4/members/caps", caps(admin=1))
    assert r.status_code == 422 and r.json()["code"] == "SELF_ALLOCATION_FORBIDDEN"


async def test_bulk_is_all_or_nothing(login, put, provisioned):
    before = await _caps(provisioned)
    r = await put(await login("dana"), f"/groups/{A1}/members/caps", caps(dani=20000, dana=1))
    assert r.status_code == 422 and r.json()["code"] == "SELF_ALLOCATION_FORBIDDEN"
    assert r.json()["details"]["rows"] == [
        {"upn": "dana@corp.example", "code": "SELF_ALLOCATION_FORBIDDEN"}
    ]
    assert await _caps(provisioned) == before


async def test_foreign_member_scope_forbidden(login, put, provisioned):
    r = await put(await login("dana"), f"/groups/{A1}/members/caps", caps(avi=100))  # in A2
    assert r.status_code == 422 and r.json()["code"] == "SCOPE_FORBIDDEN"
    r = await put(await login("dana"), f"/groups/{A1}/members/caps", caps(nobody=100))
    assert r.status_code == 422 and r.json()["code"] == "SCOPE_FORBIDDEN"


async def test_caps_permissions(login, put, provisioned):
    assert (await put(await login("viewer"), "/groups/5/members/caps", [])).status_code == 404
    assert (await put(await login("pmo"), f"/groups/{A1}/members/caps", [])).status_code == 403


async def test_concurrent_bulk_caps_one_rejected(login, put, provisioned, monkeypatch):
    dana, admin = await login("dana"), await login("admin")
    real_sleep = asyncio.sleep

    async def slow(delay, *a):  # widen the window between reading the sums and writing
        await real_sleep(max(delay, 0.3))

    monkeypatch.setattr("app.services.members.asyncio.sleep", slow)
    # each alone gives 160000 <= 250000; together 275000
    a = put(dana, f"/groups/{A1}/members/caps", caps(dani=130000))
    b = put(admin, f"/groups/{A1}/members/caps", caps(yael=130000))
    ra, rb = await asyncio.gather(a, b)
    assert sorted([ra.status_code, rb.status_code]) == [200, 422]
    assert sum((await _caps(provisioned)).values()) <= 250000
