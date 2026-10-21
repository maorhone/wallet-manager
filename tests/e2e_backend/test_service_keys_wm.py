from datetime import UTC, datetime

from app.litellm_client import LiteLLMReader, TeamUpdate
from app.services.spend import Spend, take_snapshot
from tests.api.test_service_keys import chat, create


async def test_group_key_spend_draws_from_team_budget(
    login_wm, provisioned, litellm, fake_app, ro_engine
):
    _, r = await create(login_wm)
    secret = r.json()["secret"]
    key_id = r.json()["entities"][0]["id"]
    await litellm.team_update(TeamUpdate(team_id="A1", max_budget=2))
    for _ in range(2):
        assert (await chat(fake_app, secret)).status_code == 200
    blocked = await chat(fake_app, secret)
    assert blocked.status_code == 400 and blocked.json()["error"]["scope"] == "team"
    now = datetime.now(UTC)
    await take_snapshot(provisioned, LiteLLMReader(ro_engine), now)
    async with provisioned() as db:
        spend = await Spend.load(db, now.strftime("%Y-%m"))
        assert spend.used("group", 1) == spend.used("service_key", key_id) == 2
