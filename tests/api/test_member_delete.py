import pytest
from grants import shared_grant
from lifecycle import A1, DANI, act, employee, last_id, outbox
from sqlalchemy import select

from app.litellm_client import LiteLLMPermanentError, UserDelete
from app.models import EmployeeModelCap, Grant, GrantShare
from app.outbox.worker import drain


async def test_delete_order_mapping_key_member_user(login, provisioned, litellm, prov_settings):
    token = (await employee(provisioned, DANI)).key_token_hash
    start = await last_id(provisioned)
    r = await act(await login("admin"), A1, DANI, "delete", {"confirm_upn": DANI})
    assert r.status_code == 200, r.text
    rows = await outbox(provisioned, DANI, start)
    assert [x.endpoint for x in rows] == [
        "/jwt/key/mapping/delete", "/key/delete", "/team/member_delete", "/user/delete",
    ]  # fmt: skip
    assert [x.depends_on for x in rows] == [None] + [x.id for x in rows[:3]]
    assert rows[1].payload == {"keys": [token]}
    assert rows[2].payload == {"team_id": "A1", "user_id": DANI}
    assert rows[3].payload == {"user_ids": [DANI]}
    e = await employee(provisioned, DANI)
    assert e.status == "left" and e.left_at is not None
    res = await drain(provisioned, litellm, prov_settings)
    assert (res.applied, res.failed) == (4, 0)
    assert (await litellm.key_list(DANI)).keys == []
    assert DANI not in {m.user_id for m in (await litellm.team_info("A1")).team_memberships}
    with pytest.raises(LiteLLMPermanentError) as exc:
        await litellm.user_delete(UserDelete(user_ids=[DANI]))  # the user is gone
    assert exc.value.status == 404


async def test_confirm_mismatch_422(login, provisioned):
    admin = await login("admin")
    for body in ({"confirm_upn": "yael@corp.example"}, {"confirm_upn": ""}):
        r = await act(admin, A1, DANI, "delete", body)
        assert r.status_code == 422 and r.json()["code"] == "CONFIRMATION_MISMATCH"
    r = await act(admin, A1, DANI, "delete", {})
    assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"
    assert (await employee(provisioned, DANI)).status == "active"


async def test_deleted_hidden_from_members_and_sums(login, put, provisioned):
    async with provisioned() as s:
        s.add(EmployeeModelCap(upn=DANI, model="test-model", amount=50))
        await s.commit()
    admin = await login("admin")
    before = await admin.get(f"/groups/{A1}/members")
    assert DANI in {m["upn"] for m in before.json()["members"]}
    assert (await act(admin, A1, DANI, "delete", {"confirm_upn": DANI})).status_code == 200
    after = await admin.get(f"/groups/{A1}/members")
    assert DANI not in {m["upn"] for m in after.json()["members"]}
    async with provisioned() as s:
        assert (await s.scalars(select(EmployeeModelCap))).all() == []
    # a second delete finds the employee already gone
    r = await act(admin, A1, DANI, "delete", {"confirm_upn": DANI})
    assert r.status_code == 409 and r.json()["details"] == {"status": "left"}
    # and caps can no longer be written for them
    r = await put(admin, f"/groups/{A1}/members/caps", [{"upn": DANI, "base_cap": 1}])
    assert r.status_code == 422 and r.json()["code"] == "SCOPE_FORBIDDEN"


async def test_delete_closes_shares_reason_manual(login_wm, provisioned, litellm, prov_settings):
    gid, sid = await shared_grant(login_wm, provisioned, litellm, prov_settings)
    before = (await litellm.team_info("A1")).team_info.max_budget
    r = await act(await login_wm("admin"), A1, DANI, "delete", {"confirm_upn": DANI})
    assert r.status_code == 200, r.text
    async with provisioned() as s:
        sh, g = await s.get(GrantShare, sid), await s.get(Grant, gid)
        assert (sh.status, sh.closed_reason, sh.returned) == ("closed", "manual", 3000)
        assert g.returned == 3000
    res = await drain(provisioned, litellm, prov_settings)
    assert res.failed == 0
    assert (await litellm.team_info("A1")).team_info.max_budget == before - 3000
