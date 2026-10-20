"""SPEC §7.9 FR-N1: notifications written in the same transaction as the domain event."""

from helpers.grants import new_grant
from lifecycle import A1, A2, DANI, act
from sqlalchemy import select

from app.models import Notification

DRIFTER, GHOST = "drifter@corp.example", "ghost@corp.example"


async def notes(factory, kind):
    async with factory() as s:
        q = select(Notification).where(Notification.kind == kind).order_by(Notification.id)
        return list((await s.scalars(q)).all())


async def test_pending_placement_notifies_rep_or_admins_when_unknown(
    portal, provisioned, make_client
):
    assert (await portal.post(DRIFTER)).status_code == 201  # in department D1, no budget group
    got = await notes(provisioned, "pending_placement")
    assert [(n.upn, n.scope_kind, n.scope_id) for n in got] == [
        ("rep@corp.example", "department", 1)
    ]
    assert got[0].payload["upn"] == DRIFTER and got[0].payload["reason"] == "no_budget_group"

    assert (await portal.post(GHOST)).status_code == 201  # not in the directory: department unknown
    got = await notes(provisioned, "pending_placement")
    assert [n.upn for n in got[1:]] == ["admin@corp.example"]
    assert got[1].payload["reason"] == "not_in_directory"
    await portal.post(GHOST)  # a replay writes nothing new
    assert len(await notes(provisioned, "pending_placement")) == 2


async def test_move_notifies_new_group_head(login, provisioned):
    r = await act(await login("rep"), A1, DANI, "move", {"to_group_id": A2})
    assert r.status_code == 200, r.text
    got = await notes(provisioned, "member_moved_in")
    assert [(n.upn, n.scope_id) for n in got] == [("noa@corp.example", A2)]
    assert got[0].payload["upn"] == DANI and got[0].payload["from_group_id"] == A1


async def test_grant_created_notifies_group_head(login_wm, provisioned):
    gid = await new_grant(login_wm)
    got = await notes(provisioned, "grant_received")
    assert [(n.upn, n.scope_kind, n.scope_id) for n in got] == [("dana@corp.example", "grant", gid)]
    assert got[0].payload["amount"] == 50000 and got[0].payload["group_id"] == A1
