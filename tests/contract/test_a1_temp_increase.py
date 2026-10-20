import asyncio
from datetime import UTC, datetime, timedelta

from app.litellm_client import TeamMemberUpdate

from .helpers import assert_blocked, seed_team


async def test_a1_increase_expires_without_external_action(target):
    keys = await seed_team(target, members={"u1": {"budget": 5}})
    expiry = datetime.now(UTC) + timedelta(minutes=2)
    await target.client.team_member_update(
        TeamMemberUpdate(
            team_id="team-c", user_id="u1", max_budget_in_team=5, budget_duration="1mo",
            temp_budget_increase=3, temp_budget_expiry=expiry,
        )
    )  # fmt: skip
    for _ in range(7):  # 7 spent: over the cap of 5, under 5 + 3
        assert (await target.chat(keys["u1"])).status_code == 200
    if target.name == "fake":
        r = await target.http.post("/_test/clock", json={"offset_s": 180})  # +3 min
        r.raise_for_status()
    else:
        await asyncio.sleep(150)  # the real LiteLLM needs the real time to pass
    try:
        assert_blocked(await target.chat(keys["u1"]), "team_member", target)
    finally:
        if target.name == "fake":
            await target.http.post("/_test/clock", json={"offset_s": 0})
