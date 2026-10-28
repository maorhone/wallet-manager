"""Test helper: what a group has allocated, ignoring consumption (no spend rows)."""

from app.services import commitments
from app.services.spend import Spend


async def group_allocated(db, group):
    return (await commitments.snapshot(db, group, Spend([], "2000-01"), tz="UTC")).total


async def group_committed(db, spend, group, tz="UTC"):
    snap = await commitments.snapshot(db, group, spend, tz=tz)
    return snap.total + snap.consumption_pmo


def caps(**by_upn):
    return [{"upn": f"{u}@corp.example", "base_cap": c} for u, c in by_upn.items()]


async def consume(fake_engine, amount=200000, user="dani@corp.example", team="A1"):
    """Spend that no current allocation shows (a departed member or a revoked key look the same)."""
    from datetime import UTC, datetime

    from sqlalchemy import text

    async with fake_engine.begin() as conn:
        await conn.execute(
            text(
                'INSERT INTO "LiteLLM_SpendLogs" (request_id, "user", team_id, model_group,'
                ' api_key, "startTime", "endTime", spend)'
                " VALUES (gen_random_uuid()::text, :u, :t, 'gpt-4', NULL, :at, :at, :s)"
            ),
            {"u": user, "t": team, "at": datetime.now(UTC), "s": amount},
        )
