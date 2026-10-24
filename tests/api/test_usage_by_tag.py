"""Step 5.12: spend aggregated by the project request-header tag (SoT D-059, D-063)."""

import httpx
import pytest

DANI = "dani@corp.example"


@pytest.fixture
async def tagged(provisioned, fake_app):
    rows = [
        dict(team_id="A1", user_id=DANI, model="m", amount=a, at="2026-06-02T09:00:00Z", tags=t)
        for a, t in [
            (2, ["project:alpha", "tool:claude-code"]),
            (3, ["tool:codex", "project:alpha"]),
            (5, ["project:beta"]),
            (7, None),
            (11, ["tool:codex"]),
        ]
    ]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as c:
        assert (await c.post("/_test/spend/bulk", json=rows)).status_code == 200


async def by_tag(client, **params):
    return await client.get(
        "/reports/usage",
        params={
            "scope": "group",
            "id": "1",
            "from": "2026-06-01",
            "to": "2026-06-03",
            "by": "tag",
            **params,
        },
    )


async def test_tagged_requests_aggregated_by_project(login_wm, tagged):
    r = await by_tag(await login_wm("dana"))
    assert r.status_code == 200, r.text
    got = {p["key"]: p["spend"] for p in r.json()["series"]}
    assert got["alpha"] == 5 and got["beta"] == 5


async def test_untagged_bucket(login_wm, tagged):
    got = {p["key"]: p["spend"] for p in (await by_tag(await login_wm("dana"))).json()["series"]}
    assert got["ללא תיוג"] == 18  # no tags at all, plus a tool tag without a project


async def test_tag_visibility_follows_member_rules(login_wm, tagged):
    assert (await by_tag(await login_wm("viewer"), scope="global")).status_code == 403
    me = await login_wm("dani")
    assert (await by_tag(me, scope="employee", id=DANI)).status_code == 200
    assert (await by_tag(me)).status_code in (403, 404)
