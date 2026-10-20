import asyncio

import httpx
import pytest

from app.models import Grp
from app.outbox.worker import drain

DANI = "dani@corp.example"
STEPS = ["/team/member_add", "/key/update", "/team/member_delete"]


async def _move(client):
    h = {"X-CSRF-Token": client.cookies["wm_csrf"], "X-Change-Reason": "move"}
    r = await client.post(f"/groups/1/members/{DANI}/move", json={"to_group_id": 2}, headers=h)
    assert r.status_code == 200, r.text


async def _invariants(fake_app) -> list:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as http:
        return (await http.get("/_test/invariants")).json()


async def _teams(litellm):
    key_team = (await litellm.key_list(DANI)).keys[0].team_id
    member_of = {
        t for t in ("A1", "A2")
        if DANI in {m.user_id for m in (await litellm.team_info(t)).team_memberships}
    }  # fmt: skip
    return key_team, member_of


@pytest.mark.parametrize("failing", STEPS)
async def test_a12_failure_before_each_substep_invariant_holds_then_retry_completes(
    failing, login_wm, provisioned, litellm, prov_settings, fake_app
):
    admin = await login_wm("admin")
    await _move(await login_wm("rep"))
    fake_app.state.fake.failures[failing] = [400, 1]
    res = await drain(provisioned, litellm, prov_settings)
    assert res.failed == 1 and res.held == len(STEPS) - 1 - STEPS.index(failing)
    assert await _invariants(fake_app) == []

    (row,) = (await admin.get("/outbox", params={"status": "failed"})).json()
    h = {"X-CSRF-Token": admin.cookies["wm_csrf"], "X-Change-Reason": "retry"}
    assert (await admin.post(f"/outbox/{row['id']}/retry", headers=h)).status_code == 200
    res = await drain(provisioned, litellm, prov_settings)
    assert (res.failed, res.held) == (0, 0)
    assert await _invariants(fake_app) == []
    assert await _teams(litellm) == ("A2", {"A2"})


async def test_a9_move_requests_succeed_throughout(
    login_wm, provisioned, litellm, prov_settings, fake_app, employee
):
    dani = employee(DANI)
    async with provisioned() as s:  # D4: dani gets A2's default cap, which must not be zero
        (await s.get(Grp, 2)).default_member_cap = 5000
        await s.commit()
    await _move(await login_wm("rep"))
    codes: list[int] = []
    done = asyncio.Event()

    async def requests():
        while not done.is_set():
            codes.append((await dani.chat()).status_code)
            await asyncio.sleep(0)

    async def apply():
        try:
            await drain(provisioned, litellm, prov_settings)
        finally:
            done.set()

    await asyncio.gather(requests(), apply())
    codes.append((await dani.chat()).status_code)
    assert len(codes) >= 2 and set(codes) == {200}
    assert await _teams(litellm) == ("A2", {"A2"})
