from lifecycle import A1, A2, DANI, act, employee, last_id, outbox
from sqlalchemy import select

from app.models import EmployeeModelCap, Grp
from app.outbox.worker import drain
from app.services import lifecycle


async def _prepare(factory, cap=7000):
    """A2 gets a default cap; dani gets a personal model cap and a concurrency limit."""
    async with factory() as s:
        (await s.get(Grp, A2)).default_member_cap = cap
        (await employee_row(s)).max_parallel = 3
        s.add(EmployeeModelCap(upn=DANI, model="test-model", amount=500))
        await s.commit()


async def employee_row(s):
    from app.models import Employee

    return await s.get(Employee, DANI)


async def test_move_order_add_update_delete(login, provisioned, litellm, prov_settings):
    await _prepare(provisioned)
    start = await last_id(provisioned)
    r = await act(await login("rep"), A1, DANI, "move", {"to_group_id": A2})
    assert r.status_code == 200, r.text
    rows = await outbox(provisioned, DANI, start)
    assert [x.endpoint for x in rows] == ["/team/member_add", "/key/update", "/team/member_delete"]
    assert [x.depends_on for x in rows] == [None, rows[0].id, rows[1].id]
    assert rows[2].payload == {"team_id": "A1", "user_id": DANI}  # old team fixed at enqueue
    res = await drain(provisioned, litellm, prov_settings)
    assert (res.applied, res.failed) == (3, 0)
    assert DANI in {m.user_id for m in (await litellm.team_info("A2")).team_memberships}
    assert DANI not in {m.user_id for m in (await litellm.team_info("A1")).team_memberships}
    assert (await litellm.key_list(DANI)).keys[0].team_id == "A2"


async def test_move_sets_new_default_cap_clears_model_caps(login, provisioned):
    await _prepare(provisioned)
    r = await act(await login("rep"), A1, DANI, "move", {"to_group_id": A2})
    assert r.status_code == 200
    e = await employee(provisioned, DANI)
    assert (e.group_id, e.base_cap, e.max_parallel, e.status) == (A2, 7000, 3, "active")
    async with provisioned() as s:
        assert (await s.scalars(select(EmployeeModelCap))).all() == []


async def test_move_while_rows_pending_409(login, provisioned):
    rep = await login("rep")
    assert (await act(rep, A1, DANI, "kick")).status_code == 200  # rows now pending
    r = await act(rep, A1, DANI, "move", {"to_group_id": A2})
    assert r.status_code == 409
    assert r.json()["code"] == "LIFECYCLE_IN_PROGRESS"
    assert r.json()["details"] == {"pending": 2, "failed": 0}
    assert (await employee(provisioned, DANI)).group_id == A1


async def test_move_same_group_409(login, provisioned):
    rep = await login("rep")
    r = await act(rep, A1, DANI, "move", {"to_group_id": A1})
    assert r.status_code == 409 and r.json()["code"] == "MEMBER_STATE_CONFLICT"
    async with provisioned() as s:
        (await s.get(Grp, A2)).status = "archived"
        await s.commit()
    r = await act(rep, A1, DANI, "move", {"to_group_id": A2})  # not an active group
    assert r.status_code == 409 and r.json()["code"] == "MEMBER_STATE_CONFLICT"
    r = await act(rep, A1, DANI, "move", {"to_group_id": 999})
    assert r.status_code == 404


async def test_move_calls_close_member_shares_hook(login, provisioned, monkeypatch):
    calls = []

    async def spy(ctx, upn, reason):
        calls.append((upn, reason))

    monkeypatch.setattr(lifecycle, "close_member_shares", spy)
    assert (await act(await login("rep"), A1, DANI, "move", {"to_group_id": A2})).status_code == 200
    assert calls == [(DANI, "move")]
