from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.litellm_client import (
    LiteLLMReader,
    OrganizationNew,
    TeamMemberAdd,
    TeamMemberUpdate,
    TeamNew,
)
from app.litellm_client.models import Member


async def _org(litellm, org_id, budget):
    await litellm.organization_new(
        OrganizationNew(
            organization_id=org_id, organization_alias=org_id.upper(), max_budget=budget
        )
    )


async def _team(litellm, team_id, org_id, budget):
    await litellm.team_new(
        TeamNew(
            team_id=team_id, team_alias=team_id.upper(), organization_id=org_id, max_budget=budget
        )
    )


async def _member(litellm, team_id, user, cap, temp=None, expiry_days=None):
    await litellm.team_member_add(
        TeamMemberAdd(
            team_id=team_id, member=Member(user_id=user, role="user"), max_budget_in_team=cap
        )
    )
    if temp:
        await litellm.team_member_update(
            TeamMemberUpdate(
                team_id=team_id,
                user_id=user,
                max_budget_in_team=cap,
                budget_duration="1mo",
                temp_budget_increase=temp,
                temp_budget_expiry=datetime.now(UTC) + timedelta(days=expiry_days),
            )
        )


async def test_overallocated_teams_query_11_3(litellm, ro_engine):
    await _org(litellm, "org", 1000)
    for team in ("ok", "over", "expired"):
        await _team(litellm, team, "org", 100)
    await _member(litellm, "ok", "a", 60)
    await _member(litellm, "ok", "b", 40)  # exactly at the budget: not flagged
    await _member(litellm, "over", "c", 60)
    await _member(litellm, "over", "d", 30, temp=20, expiry_days=2)  # 60+30+20 = 110
    await _member(litellm, "expired", "e", 60)
    await _member(litellm, "expired", "f", 30, temp=50, expiry_days=-1)  # expired: 90

    rows = await LiteLLMReader(ro_engine).overallocated_teams()
    assert [(r.team_id, r.team_alias, r.group_budget, r.allocated) for r in rows] == [
        ("over", "OVER", Decimal(100), Decimal(110))
    ]


async def test_org_allocations_query_11_3(litellm, ro_engine):
    await _org(litellm, "big", 100)
    await _org(litellm, "small", 100)
    await _org(litellm, "empty", 100)
    await _team(litellm, "t1", "big", 60)
    await _team(litellm, "t2", "big", 50)
    await _team(litellm, "t3", "small", 30)

    rows = {r.organization_id: r for r in await LiteLLMReader(ro_engine).org_allocations()}
    # no HAVING: within-budget orgs are returned too; orgs without teams are not
    assert set(rows) == {"big", "small"}
    assert (rows["big"].organization_alias, rows["big"].dept_budget, rows["big"].allocated) == (
        "BIG",
        Decimal(100),
        Decimal(110),
    )
    assert rows["small"].allocated == Decimal(30)


async def test_spend_rows(litellm, fake_engine, ro_engine):
    async with fake_engine.begin() as conn:
        await conn.execute(
            text(
                'INSERT INTO "LiteLLM_SpendLogs" (request_id, api_key, "user", team_id,'
                ' model_group, "startTime", "endTime", spend) VALUES '
                "('r1','k','u@x','t','m1','2026-03-01 10:00+00','2026-03-01 10:01+00',1.5),"
                "('r2','k','u@x','t','m1','2026-03-01 20:00+00','2026-03-01 20:01+00',0.25),"
                "('r3','k','u@x','t','m1','2026-04-01 00:00+00','2026-04-01 00:01+00',9)"
            )
        )
    start = datetime(2026, 3, 1, tzinfo=UTC)
    rows = await LiteLLMReader(ro_engine).spend_rows(start, datetime(2026, 4, 1, tzinfo=UTC))
    assert [tuple(r) for r in rows] == [
        ("t", "u@x", "k", "m1", start.date(), Decimal("1.75"), 0, 0)
    ]


async def test_ping(ro_engine):
    assert await LiteLLMReader(ro_engine).ping() is True


async def test_readonly_role_cannot_write(litellm, ro_engine):
    await _org(litellm, "org", 100)
    async with ro_engine.connect() as conn:
        assert await conn.scalar(text('SELECT count(*) FROM "LiteLLM_OrganizationTable"')) == 1
    for stmt in (
        'UPDATE "LiteLLM_BudgetTable" SET max_budget = 1',
        'DELETE FROM "LiteLLM_TeamTable"',
        "CREATE TABLE evil (x int)",
    ):
        async with ro_engine.connect() as conn:
            with pytest.raises(DBAPIError, match="permission denied"):
                await conn.execute(text(stmt))
