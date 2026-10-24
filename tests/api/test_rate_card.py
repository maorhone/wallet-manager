"""Step 5.11: rate card and metered usage value (SoT D-049, D-054)."""

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.litellm_client import LiteLLMReader
from app.services.rate_card import metered_value
from app.services.spend import take_snapshot

NOW = datetime(2026, 6, 15, 12, tzinfo=ZoneInfo("Asia/Jerusalem"))
RATES = {
    "rates": [
        {"model": "gpt-4", "input_per_1m": "10", "output_per_1m": "30"},
        {"model": "claude", "input_per_1m": "3", "output_per_1m": "15"},
    ]
}


async def _log(fake_engine, i, model, pt, ct):
    async with fake_engine.begin() as conn:
        await conn.execute(
            text(
                'INSERT INTO "LiteLLM_SpendLogs" (request_id, "user", team_id, model_group,'
                ' "startTime", "endTime", spend, prompt_tokens, completion_tokens)'
                " VALUES (:i, 'dani@corp.example', 'A1', :m, :at, :at, 1, :p, :c)"
            ),
            {"i": f"r{i}", "m": model, "at": datetime(2026, 6, 3, 9, tzinfo=UTC), "p": pt, "c": ct},
        )


async def test_metered_value_from_tokens_and_rates(
    login, put, seeded, fake_app, fake_engine, ro_engine
):
    admin = await login("admin")
    assert (await put(admin, "/rate-card", RATES)).status_code == 200
    got = (await admin.get("/rate-card")).json()["rates"]
    assert {r["model"] for r in got} == {"gpt-4", "claude"}
    await _log(fake_engine, 1, "gpt-4", 2_000_000, 1_000_000)
    await _log(fake_engine, 2, "claude", 1_000_000, 0)
    await take_snapshot(seeded, LiteLLMReader(ro_engine), NOW)
    async with seeded() as s:
        assert await metered_value(s, "group", 1, "2026-06") == Decimal(53)  # 20+30+3


async def test_missing_rate_returns_null(login, put, seeded, fake_app, fake_engine, ro_engine):
    admin = await login("admin")
    await put(admin, "/rate-card", {"rates": RATES["rates"][:1]})
    await _log(fake_engine, 1, "gpt-4", 1_000_000, 0)
    await _log(fake_engine, 2, "claude", 1000, 0)
    await take_snapshot(seeded, LiteLLMReader(ro_engine), NOW)
    async with seeded() as s:
        assert await metered_value(s, "group", 1, "2026-06") is None


async def test_rate_card_admin_only(login, put, provisioned):
    head = await login("dana")
    assert (await put(head, "/rate-card", RATES)).status_code == 403
