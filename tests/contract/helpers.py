"""Shared helpers for contract tests that run against the fake and (optionally) a real LiteLLM."""

from dataclasses import dataclass

import httpx

from app.litellm_client import (
    KeyGenerate,
    LiteLLMClient,
    LiteLLMReader,
    OrganizationNew,
    TeamMemberAdd,
    TeamMemberUpdate,
    TeamNew,
    UserNew,
)
from app.litellm_client.models import Member, ModelBudget


@dataclass
class Target:
    name: str  # "fake" | "real"
    client: LiteLLMClient
    reader: LiteLLMReader
    http: httpx.AsyncClient
    model: str
    master_key: str = ""

    async def chat(self, key: str, model: str | None = None) -> httpx.Response:
        return await self.http.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model or self.model, "messages": [{"role": "user", "content": "hi"}]},
        )

    async def configure(self, latency_ms: int = 0, cost_per_request: float = 1) -> None:
        """Fake only. The real target must be set up so each request takes the latency/cost."""
        if self.name == "fake":
            r = await self.http.post(
                "/_test/config",
                json={"latency_ms": latency_ms, "cost_per_request": cost_per_request},
            )
            r.raise_for_status()


def assert_blocked(resp: httpx.Response, scope: str, target: Target) -> None:
    """Fake: structured ``error.scope``. Real LiteLLM: HTTP 400 with a budget message."""
    body = resp.json()
    assert resp.status_code == 400, (resp.status_code, body)
    err = body["error"]
    if target.name == "fake":
        assert err["type"] == "budget_exceeded" and err["scope"] == scope, err
    else:
        assert "budget" in str(err.get("message", err)).lower(), err


async def seed_team(
    t: Target,
    *,
    team_budget: float | None = None,
    team_models: dict[str, float] | None = None,
    members: dict[str, dict] | None = None,
    org: str | None = None,
) -> dict[str, str]:
    """Create org (optional), team and members; return ``{user_id: plaintext_key}``.

    ``members[user] = {"budget": float, "models": {model: cap}, "parallel": int}``.
    """
    c = t.client
    if org:
        await c.organization_new(
            OrganizationNew(organization_id=org, organization_alias=org, max_budget=1000)
        )
    await c.team_new(
        TeamNew(
            team_id="team-c",
            team_alias="c",
            organization_id=org,
            max_budget=team_budget,
            model_max_budget={
                m: ModelBudget(budget_limit=v, time_period="1mo")
                for m, v in (team_models or {}).items()
            }
            or None,
        )
    )
    keys = {}
    for uid, cfg in (members or {}).items():
        await c.user_new(UserNew(user_id=uid, sso_user_id=uid, user_email=f"{uid}@x.test"))
        await c.team_member_add(TeamMemberAdd(team_id="team-c", member=Member(user_id=uid)))
        await c.team_member_update(
            TeamMemberUpdate(
                team_id="team-c",
                user_id=uid,
                max_budget_in_team=cfg.get("budget", 1000),
                budget_duration="1mo",
                model_max_budget={
                    m: ModelBudget(budget_limit=v, time_period="1mo")
                    for m, v in cfg.get("models", {}).items()
                }
                or None,
            )
        )
        gen = await c.key_generate(
            KeyGenerate(user_id=uid, team_id="team-c", max_parallel_requests=cfg.get("parallel"))
        )
        keys[uid] = gen.key  # plaintext: held in memory for this test only
    return keys
