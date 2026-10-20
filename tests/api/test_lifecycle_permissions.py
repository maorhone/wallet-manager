"""SPEC §9.4: who may kick, restore, move and delete whom."""

import pytest
from lifecycle import A1, A2, DANA, DANI, YAEL, act, set_status

ADMIN = "admin@corp.example"
ACTIONS = ("kick", "restore", "move", "delete")
OK, FORBIDDEN, NOT_FOUND = 200, "FORBIDDEN", "NOT_FOUND"
SCOPE, SELF = "SCOPE_FORBIDDEN", "SELF_ALLOCATION_FORBIDDEN"
HEAD_DN = "CN=wm-head-a1,OU=Groups,DC=corp,DC=example"

# (actor, group, target, expected per action in ACTIONS order)
CASES = {
    "admin": ("admin", A1, DANI, (OK, OK, OK, OK)),
    "rep": ("rep", A1, DANI, (OK, OK, OK, FORBIDDEN)),
    "head_own_group": ("dana", A1, DANI, (OK, OK, FORBIDDEN, FORBIDDEN)),
    "head_other_group": ("dana", A2, "avi@corp.example", (NOT_FOUND,) * 4),
    "head_on_cohead": ("dana", A1, YAEL, (SCOPE, SCOPE, FORBIDDEN, FORBIDDEN)),
    "head_on_self": ("dana", A1, DANA, (SELF, SELF, FORBIDDEN, FORBIDDEN)),
    "pmo": ("pmo", A1, DANI, (FORBIDDEN,) * 4),
    "viewer": ("viewer", A1, DANI, (FORBIDDEN,) * 4),
    "plain_employee": ("dani", A1, DANI, (NOT_FOUND,) * 4),
    "admin_on_self": ("admin", 4, ADMIN, (SELF,) * 4),
    "rep_on_admin": ("rep", 4, ADMIN, (SCOPE, SCOPE, SCOPE, FORBIDDEN)),
    "rep_on_self": ("rep", 4, "rep@corp.example", (SELF, SELF, SELF, FORBIDDEN)),
}


def _body(action, upn):
    return {"to_group_id": A2 if group_of(upn) != A2 else A1} if action == "move" else (
        {"confirm_upn": upn} if action == "delete" else {}
    )  # fmt: skip


def group_of(upn):
    return A2 if upn.startswith("avi") else 1


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("case", CASES)
async def test_matrix(case, action, login, provisioned):
    who, group, target, expected = CASES[case]
    want = expected[ACTIONS.index(action)]
    client = await login(who)
    if case == "head_on_cohead":  # yael also holds the head role of A1 in the directory
        client._transport.app.state.directory.users[YAEL]["groups"].append(HEAD_DN)
    # restore needs a blocked target; the other actions work on an active or blocked one
    await set_status(provisioned, target, "blocked" if action != "kick" else "active")
    r = await act(client, group, target, action, _body(action, target))
    if want == OK:
        assert r.status_code == 200, r.text
    else:
        assert r.status_code == {FORBIDDEN: 403, NOT_FOUND: 404}.get(want, 422), r.text
        assert r.json()["code"] == want
    if want == SELF:
        assert "owner_role" in (r.json().get("details") or {}) or r.json()["code"] == SELF


async def test_rep_cannot_move_into_other_department_admin_can(login, provisioned):
    body = {"to_group_id": 5}  # C1 in D2
    r = await act(await login("rep"), A1, DANI, "move", body)
    assert r.status_code == 422 and r.json()["code"] == "SCOPE_FORBIDDEN"
    r = await act(await login("admin"), A1, DANI, "move", body)
    assert r.status_code == 200, r.text


async def test_move_targets_follow_scope(login, provisioned):
    path = f"/groups/{A1}/members/{DANI}/move-targets"
    rep = (await (await login("rep")).get(path)).json()
    assert {t["group_id"] for t in rep} == {2, 3, 4}  # D1's other groups
    assert set(rep[0]) == {"group_id", "name", "department_id", "department_name"}
    admin = (await (await login("admin")).get(path)).json()
    assert {t["group_id"] for t in admin} == {2, 3, 4, 5, 6}
    assert (await (await login("dana")).get(path)).status_code == 403  # heads cannot move
    assert (await (await login("dani")).get(path)).status_code == 404


async def test_members_rows_carry_actions(login, provisioned):
    async def rows(who):
        r = await (await login(who)).get(f"/groups/{A1}/members")
        return {m["upn"]: m["actions"] for m in r.json()["members"]}

    none = dict.fromkeys(ACTIONS, False)
    head = await rows("dana")
    assert head[DANI] == {**none, "kick": True}
    assert head[YAEL] == {**none, "kick": True}  # a plain member until the directory says else
    assert head[DANA] == none  # own row: no actions
    rep = await rows("rep")
    assert rep[DANI] == {**none, "kick": True, "move": True}
    admin = await rows("admin")
    assert admin[DANI] == dict.fromkeys(ACTIONS, True) | {"restore": False}
    await set_status(provisioned, DANI, "blocked")
    assert (await rows("admin"))[DANI] == dict.fromkeys(ACTIONS, True) | {"kick": False}
