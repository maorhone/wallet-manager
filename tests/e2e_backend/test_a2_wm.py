from app.litellm_client import TeamMemberUpdate, TeamUpdate


async def test_a2_bypass_then_report_flags(login_wm, provisioned, litellm, employee):
    # Bypass the Wallet Manager: member caps above the team budget, written straight to LiteLLM.
    await litellm.team_update(TeamUpdate(team_id="A1", max_budget=100))
    for upn in ("dani@corp.example", "yael@corp.example"):
        await litellm.team_member_update(
            TeamMemberUpdate(
                team_id="A1", user_id=upn, max_budget_in_team=60, budget_duration="1mo"
            )
        )
    dani, yael = employee("dani@corp.example"), employee("yael@corp.example")
    for who, n in ((dani, 60), (yael, 40)):  # one at a time: the provisioned max_parallel is 4
        for _ in range(n):
            assert (await who.chat()).status_code == 200
    blocked = await yael.chat()
    assert blocked.status_code == 400 and blocked.json()["error"]["scope"] == "team"

    r = await (await login_wm("admin")).get("/reports/overallocation")
    assert r.status_code == 200, r.text
    assert any(
        (f["source"], f["level"], f["entity_id"]) == ("litellm", "group", 1)
        for f in r.json()["findings"]
    )
