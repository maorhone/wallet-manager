"""§11.1 PUT /policies, §12.5.5 Policies."""

from sqlalchemy import select

from app.models import AuditLog, Grp

A1 = 1  # base 250000; members dana (head), dani, yael at 15000 each


def caps(**by_upn):
    return [{"upn": f"{u}@corp.example", "base_cap": c} for u, c in by_upn.items()]


async def test_admin_updates_concurrency_range_and_audits(login, put, provisioned):
    admin = await login("admin")
    r = await put(admin, "/policies", {"concurrency": {"min": 1, "default": 5, "max": 10}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entities"] == [
        {
            "entity": "policy", "id": "concurrency", "sync_status": "applied",
            "values": {"concurrency": {"min": 1, "default": 5, "max": 10}},
        }
    ]  # fmt: skip

    updated = (await admin.get("/policies")).json()
    assert updated["concurrency"] == {"min": 1, "default": 5, "max": 10}

    async with provisioned() as s:
        row = (
            await s.scalars(
                select(AuditLog).where(
                    AuditLog.entity == "policy", AuditLog.entity_id == "concurrency"
                )
            )
        ).one()
        assert row.after == {"concurrency": {"min": 1, "default": 5, "max": 10}}
        assert row.before == {"concurrency": {"min": 1, "default": 4, "max": 8}}


async def test_min_above_max_invalid(login, put, provisioned):
    admin = await login("admin")
    r = await put(admin, "/policies", {"rpm": {"min": 100, "default": 50, "max": 600}})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "INVALID_REQUEST"
    assert body["details"]["field"] == "rpm"


async def test_unknown_key_invalid(login, put, provisioned):
    admin = await login("admin")
    r = await put(admin, "/policies", {"bogus_key": 1})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "INVALID_REQUEST"
    assert body["details"]["key"] == "bogus_key"

    member_defaults = await put(admin, "/policies", {"member_defaults.group.1": {}})
    assert member_defaults.status_code == 422
    assert member_defaults.json()["details"]["key"] == "member_defaults.group.1"


async def test_non_admin_403(login, put, provisioned):
    r = await put(
        await login("rep"), "/policies", {"concurrency": {"min": 1, "default": 5, "max": 10}}
    )
    assert r.status_code == 403


async def test_overalloc_factor_per_group_then_caps_check_uses_it(login, put, provisioned):
    admin = await login("admin")
    r = await put(admin, "/policies", {"overalloc_factors": {str(A1): 1.2}})
    assert r.status_code == 200, r.text
    assert r.json()["entities"] == [
        {
            "entity": "group", "id": str(A1), "sync_status": "applied",
            "values": {"overalloc_factor": 1.2},
        }
    ]  # fmt: skip
    async with provisioned() as s:
        assert float((await s.get(Grp, A1)).overalloc_factor) == 1.2

    dana = await login("dana")
    assert (await put(dana, f"/groups/{A1}/members/caps", caps(dani=250000))).status_code == 200
    over = await put(dana, f"/groups/{A1}/members/caps", caps(yael=40000))  # 305000 > 300000
    assert over.status_code == 422 and over.json()["available"] == 300000

    unknown_group = await put(admin, "/policies", {"overalloc_factors": {"99999": 1.2}})
    assert unknown_group.status_code == 422
    assert unknown_group.json()["details"]["group_id"] == 99999

    out_of_range = await put(admin, "/policies", {"overalloc_factors": {str(A1): 3.0}})
    assert out_of_range.status_code == 422
    assert out_of_range.json()["code"] == "INVALID_REQUEST"
