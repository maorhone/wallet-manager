import httpx
from sqlalchemy import select

from fake_litellm.db import spend_logs, team


async def test_bulk_spend_times_key_and_atomic_rollback(provisioned, fake_app, fake_engine):
    body = dict(
        team_id="A1",
        user_id="dani@corp.example",
        model="m",
        amount=2,
        at="2026-06-01T10:00:00Z",
        api_key="dummy-test-hash",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_app), base_url="http://fake"
    ) as c:
        assert (await c.post("/_test/spend", json=body)).status_code == 200
        r = await c.post("/_test/spend/bulk", json=[body, {**body, "team_id": "missing"}])
        assert r.status_code == 404
        r = await c.post(
            "/_test/spend/bulk",
            json=[
                body,
                {
                    **body,
                    "start": "2026-06-01T11:00:00Z",
                    "end": "2026-06-01T10:00:00Z",
                },
            ],
        )
        assert r.status_code == 422
    async with fake_engine.connect() as conn:
        rows = (await conn.execute(select(spend_logs))).mappings().all()
        assert len(rows) == 1
        assert rows[0]["startTime"] == rows[0]["endTime"]
        assert rows[0]["api_key"] == body["api_key"]
        assert await conn.scalar(select(team.c.spend).where(team.c.team_id == "A1")) == 2
