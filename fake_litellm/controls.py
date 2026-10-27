"""Test-only controls, mounted when FAKE_TEST_CONTROLS=1 (the fake's default)."""

from datetime import datetime
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import AwareDatetime, BaseModel
from sqlalchemy import exists, insert, select

from .db import error_logs, reset_db, spend_logs, team, team_member, token
from .enforcement import bump_spend

router = APIRouter(prefix="/_test")


class Outage(BaseModel):
    on: bool


class Config(BaseModel):
    latency_ms: int = 0
    cost_per_request: float = 1
    redis: Literal["none", "ok", "down"] = "none"


class Fail(BaseModel):
    endpoint: str
    status: Literal[400, 500]
    times: int = 1


class Spend(BaseModel):
    team_id: str
    user_id: str | None = None
    model: str
    amount: float
    at: AwareDatetime | None = None
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    api_key: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tags: list[str] | None = None


class ErrorRow(BaseModel):
    team_id: str | None = None
    user_id: str | None = None
    model: str | None = None
    status_code: int
    exception_type: str = ""
    message: str = ""
    retry_after: int | None = None
    at: AwareDatetime | None = None
    key_hash: str | None = None


@router.post("/errors")
async def add_errors(body: list[ErrorRow], request: Request):
    now = request.app.state.fake.now()
    async with request.app.state.fake.engine.begin() as conn:
        for e in body:
            await conn.execute(
                insert(error_logs).values(
                    request_id=uuid4().hex,
                    startTime=e.at or now,
                    endTime=e.at or now,
                    model_group=e.model,
                    status_code=str(e.status_code),
                    exception_type=e.exception_type,
                    exception_string=e.message,
                    request_kwargs={
                        "litellm_params": {
                            "metadata": {
                                "user_api_key_user_id": e.user_id,
                                "user_api_key_team_id": e.team_id,
                                "user_api_key_hash": e.key_hash,
                            }
                        },
                        "retry_after": e.retry_after,
                    },
                )
            )
    return {"ok": True}


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
    request.app.state.fake.redis = body.redis
    return body


@router.post("/model_health")
async def model_health(body: dict[str, str | None], request: Request):
    """Replace the per-model probe results served by ``/health`` (value = error text or null)."""
    request.app.state.fake.model_health = dict(body)
    return body


class Clock(BaseModel):
    offset_s: float


@router.post("/clock")
async def set_clock(body: Clock, request: Request):
    """Move the fake's clock ``offset_s`` seconds ahead of the wall clock (R2 expiry)."""
    request.app.state.fake.clock_offset_s = body.offset_s
    return body


@router.get("/clock")
async def get_clock(request: Request):
    fake = request.app.state.fake
    return {"offset_s": fake.clock_offset_s, "now": fake.now()}


@router.post("/fail")
async def fail(body: Fail, request: Request):
    """Fail the next ``times`` calls to ``endpoint`` (500 = transient, 400 = permanent)."""
    request.app.state.fake.failures[body.endpoint] = [body.status, body.times]
    return body


@router.get("/invariants")
async def invariants(request: Request):
    """Keys whose team has no membership for the key's user (a key outliving its membership)."""
    async with request.app.state.fake.engine.connect() as conn:
        rows = await conn.execute(
            select(token.c.token, token.c.user_id, token.c.team_id)
            .where(
                token.c.team_id.is_not(None),
                ~exists().where(
                    team_member.c.team_id == token.c.team_id,
                    team_member.c.user_id == token.c.user_id,
                ),
            )
            .order_by(token.c.token)
        )
        return [dict(r) for r in rows.mappings()]


@router.post("/spend")
async def add_spend(body: Spend, request: Request):
    """Insert a SpendLogs row and bump team / membership / organization spend consistently."""
    async with request.app.state.fake.engine.begin() as conn:
        await _add_spend(conn, body, request.app.state.fake.now())
    return {"ok": True}


@router.post("/spend/bulk")
async def add_spend_bulk(body: list[Spend], request: Request):
    """All rows and spend counters commit together, including failures halfway through."""
    now = request.app.state.fake.now()
    async with request.app.state.fake.engine.begin() as conn:
        for item in body:
            await _add_spend(conn, item, now)
    return {"ok": True}


async def _add_spend(conn, body: Spend, now: datetime):
    at = body.at or now
    start, end = body.start or at, body.end or at
    if end < start:
        raise HTTPException(422, detail={"error": "end precedes start"})
    row = (
        await conn.execute(select(team.c.organization_id).where(team.c.team_id == body.team_id))
    ).first()
    if row is None:
        raise HTTPException(404, detail={"error": f"team {body.team_id} not found"})
    await conn.execute(
        insert(spend_logs).values(
            request_id=uuid4().hex,
            api_key=body.api_key,
            user=body.user_id,
            team_id=body.team_id,
            organization_id=row[0],
            model_group=body.model,
            startTime=start,
            endTime=end,
            spend=body.amount,
            prompt_tokens=body.prompt_tokens,
            completion_tokens=body.completion_tokens,
            request_tags=body.tags,
        )
    )
    await bump_spend(conn, body.api_key, body.team_id, body.user_id, row[0], body.amount)
