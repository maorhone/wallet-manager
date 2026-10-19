"""Test-only controls, mounted when FAKE_TEST_CONTROLS=1 (the fake's default)."""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import insert, select

from .db import reset_db, spend_logs, team
from .enforcement import bump_spend

router = APIRouter(prefix="/_test")


class Outage(BaseModel):
    on: bool


class Config(BaseModel):
    latency_ms: int = 0
    cost_per_request: float = 1


class Spend(BaseModel):
    team_id: str
    user_id: str | None = None
    model: str
    amount: float
    at: datetime | None = None


@router.post("/reset")
async def reset(request: Request):
    await reset_db(request.app.state.fake.engine)
    request.app.state.fake.reset()
    return {"ok": True}


@router.post("/outage")
async def outage(body: Outage, request: Request):
    request.app.state.fake.outage = body.on
    return {"outage": body.on}


@router.post("/config")
async def config(body: Config, request: Request):
    request.app.state.fake.latency_ms = body.latency_ms
    request.app.state.fake.cost_per_request = body.cost_per_request
    return body


@router.post("/spend")
async def add_spend(body: Spend, request: Request):
    """Insert a SpendLogs row and bump team / membership / organization spend consistently."""
    at = body.at or datetime.now(UTC)
    async with request.app.state.fake.engine.begin() as conn:
        row = (
            await conn.execute(select(team.c.organization_id).where(team.c.team_id == body.team_id))
        ).first()
        if row is None:
            raise HTTPException(404, detail={"error": f"team {body.team_id} not found"})
        await conn.execute(
            insert(spend_logs).values(
                request_id=uuid4().hex,
                user=body.user_id,
                team_id=body.team_id,
                organization_id=row[0],
                model_group=body.model,
                startTime=at,
                endTime=at,
                spend=body.amount,
            )
        )
        await bump_spend(conn, None, body.team_id, body.user_id, row[0], body.amount)
    return {"ok": True}
