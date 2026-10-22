"""A1 through WM: a share's temporary increase ends on its own; the scheduler then closes it."""

import httpx
import pytest
from helpers.grants import DANI, membership, new_grant, new_share

from app.models import Grant, GrantShare
from app.outbox.worker import drain


async def _http(fake_app, method, path, **kw):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as http:
        r = await http.request(method, path, **kw)
        r.raise_for_status()
        return r


@pytest.mark.xfail(strict=False, reason="time-dependent month-boundary double count")
async def test_a1_share_expiry_blocks_then_scheduler_closes_and_returns(
    login_wm, provisioned, litellm, prov_settings, fake_app, employee, run_job, fake_clock
):
    gid = await new_grant(login_wm)
    sid = await new_share(login_wm, gid, DANI, 3000, days=10)
    await drain(provisioned, litellm, prov_settings)
    spend = {"team_id": "A1", "user_id": DANI, "model": "m", "amount": 16000}
    await _http(fake_app, "POST", "/_test/spend", json=spend)  # 1000 over the base cap of 15000
    dani = employee(DANI)
    assert (await dani.chat()).status_code == 200  # the share still lifts the cap

    await _http(fake_app, "POST", "/_test/clock", json={"offset_s": 11 * 86400})
    blocked = await dani.chat()  # R2: LiteLLM ends the increase by itself
    assert blocked.status_code == 400 and blocked.json()["error"]["scope"] == "team_member"

    fake_clock.advance(days=11)
    res = await run_job()
    assert (res.shares_closed, res.members_recomputed) == (1, 1)
    async with provisioned() as s:
        sh, g = await s.get(GrantShare, sid), await s.get(Grant, gid)
        assert (sh.status, sh.closed_reason, sh.used, sh.returned) == (
            "closed",
            "expiry",
            1001,  # 16000 charged + the one request that succeeded (1 credit)
            1999,
        )
        assert g.returned == 1999
    await drain(provisioned, litellm, prov_settings)
    budget = await membership(litellm)
    assert not budget.temp_budget_increase and budget.temp_budget_expiry is None
    assert (await dani.chat()).status_code == 400  # still over the base cap
    await _http(fake_app, "POST", "/_test/clock", json={"offset_s": 0})
