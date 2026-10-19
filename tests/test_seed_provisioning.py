import httpx
from sqlalchemy import func, select

from app.models import AuditLog, LitellmWrite
from app.seed import seed
from fake_litellm.enforcement import make_test_jwt


async def test_seed_creates_org_team_user_member_key_mapping(
    provisioned, litellm, fake_app, prov_settings
):
    assert (await litellm.organization_info("D2")).organization_alias == "Department D2"
    team = await litellm.team_info("A1")
    assert team.team_info.max_budget == 250000
    members = {m.user_id: m for m in team.team_memberships}
    assert members["dana@corp.example"].litellm_budget_table.max_budget == 15000
    assert (await litellm.key_list("dana@corp.example")).keys

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as http:
        ok = await http.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {make_test_jwt('dana@corp.example')}"},
            json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert ok.status_code == 200, ok.text

    # Re-seeding enqueues nothing new; audit actor is the seed.
    async with provisioned() as s:
        before = await s.scalar(select(func.count()).select_from(LitellmWrite))
        actors = set(await s.scalars(select(AuditLog.actor_upn)))
        roles = set(await s.scalars(select(AuditLog.actor_role)))
    await seed(provisioned, prov_settings.seed_file, settings=prov_settings)
    async with provisioned() as s:
        assert await s.scalar(select(func.count()).select_from(LitellmWrite)) == before
    assert actors == {"seed"} and roles == {"sync_service"}
