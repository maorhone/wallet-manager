"""Fake runtime enforcement (SPEC §3.1 R1/R2/R5-R7, §3.2 R10/R11): POST /chat/completions."""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from .db import budget, jwt_mapping, organization, spend_logs, team, team_member, token
from .state import FakeState

JWT_ISSUER = "https://adfs.test"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwt_secret() -> str:
    return os.environ.get("FAKE_JWT_SECRET", "fake-jwt-secret")


def make_test_jwt(upn: str, iss: str = JWT_ISSUER, secret: str | None = None) -> str:
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps({"iss": iss, "upn": upn, "exp": int(time.time()) + 3600}).encode())
    sig = hmac.new((secret or _jwt_secret()).encode(), f"{head}.{body}".encode(), hashlib.sha256)
    return f"{head}.{body}.{_b64(sig.digest())}"


def _claims(jwt: str) -> dict | None:
    try:
        head, body, sig = jwt.split(".")
        good = hmac.new(_jwt_secret().encode(), f"{head}.{body}".encode(), hashlib.sha256)
        if not hmac.compare_digest(_b64(good.digest()), sig):
            return None
        return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError:
        return None


def _error(status: int, type_: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": {"type": type_, "message": message, **extra}}, status_code=status)


class _Reject(Exception):
    def __init__(self, resp: JSONResponse):
        self.resp = resp


def _blocked(scope: str, what: str) -> _Reject:
    return _Reject(_error(400, "budget_exceeded", f"Budget exceeded for {what}", scope=scope))


async def _authenticate(conn: AsyncConnection, auth: str) -> dict:
    bearer = auth.removeprefix("Bearer ").strip()
    if bearer.startswith("sk-"):
        tok = hashlib.sha256(bearer.encode()).hexdigest()
    else:
        claims = _claims(bearer)
        if claims is None:
            raise _Reject(_error(401, "auth_error", "invalid token"))
        mapped = (
            await conn.execute(
                select(jwt_mapping.c.token).where(
                    jwt_mapping.c.jwt_issuer == claims.get("iss"),
                    jwt_mapping.c.jwt_claim_name == "upn",
                    jwt_mapping.c.jwt_claim_value == claims.get("upn"),
                    jwt_mapping.c.is_active.is_(True),  # inactive = unmapped (V6)
                )
            )
        ).scalar()
        if mapped is None:  # R11: unmapped identities are rejected
            raise _Reject(_error(403, "forbidden", "no key mapped for this identity"))
        tok = mapped
    row = (await conn.execute(select(token).where(token.c.token == tok))).mappings().first()
    if row is None:
        raise _Reject(_error(401, "auth_error", "invalid key"))
    if row["blocked"]:
        raise _Reject(_error(401, "auth_error", "key is blocked"))
    return dict(row)


async def _model_spend(conn: AsyncConnection, model: str, **where) -> float:
    q = select(func.coalesce(func.sum(spend_logs.c.spend), 0)).where(
        spend_logs.c.model_group == model
    )
    for col, val in where.items():
        q = q.where(spend_logs.c[col] == val)
    return float((await conn.execute(q)).scalar_one())


def _cap(models: dict | None, model: str) -> float | None:
    entry = (models or {}).get(model)
    return None if entry is None else float(entry["budget_limit"])


async def _check_budgets(conn: AsyncConnection, key: dict, model: str, now: datetime) -> None:
    """Order: key, team member, team, organization, then model budgets (R1, R5, R6)."""
    if key["max_budget"] is not None and key["spend"] >= key["max_budget"]:
        raise _blocked("key", "key")
    team_id, user_id = key["team_id"], key["user_id"]
    if team_id is None:
        return
    t = (await conn.execute(select(team).where(team.c.team_id == team_id))).mappings().first()
    if t is None:
        return
    mem = (
        (
            await conn.execute(
                select(team_member, budget)
                .select_from(
                    team_member.outerjoin(budget, budget.c.budget_id == team_member.c.budget_id)
                )
                .where(team_member.c.team_id == team_id, team_member.c.user_id == user_id)
            )
        )
        .mappings()
        .first()
    )
    if mem and mem["max_budget"] is not None:
        limit = mem["max_budget"]
        expiry = mem["temp_budget_expiry"]
        if expiry is not None and expiry > now:  # R2
            limit += mem["temp_budget_increase"] or 0
        if (mem["spend"] or 0) >= limit:
            raise _blocked("team_member", "team member")
    if t["max_budget"] is not None and t["spend"] >= t["max_budget"]:
        raise _blocked("team", "team")
    if t["organization_id"]:
        row = (
            (
                await conn.execute(
                    select(organization.c.spend, budget.c.max_budget)
                    .select_from(
                        organization.join(budget, budget.c.budget_id == organization.c.budget_id)
                    )
                    .where(organization.c.organization_id == t["organization_id"])
                )
            )
            .mappings()
            .first()
        )
        if row and row["max_budget"] is not None and row["spend"] >= row["max_budget"]:
            raise _blocked("organization", "organization")
    # R5/R6: a key-level entry for the model overrides the team entry.
    key_cap = _cap(key["model_max_budget"], model)
    if key_cap is not None:
        if await _model_spend(conn, model, api_key=key["token"]) >= key_cap:
            raise _blocked("key", "key model")
    elif (cap := _cap(t["model_max_budget"], model)) is not None:
        if await _model_spend(conn, model, team_id=team_id) >= cap:
            raise _blocked("team_model", "team model")
    if mem and (cap := _cap(mem["model_max_budget"], model)) is not None:
        if await _model_spend(conn, model, team_id=team_id, user=user_id) >= cap:
            raise _blocked("member_model", "member model")


async def _charge(
    conn: AsyncConnection, key: dict, model: str, cost: float, start: datetime, end: datetime
) -> None:
    team_id = key["team_id"]
    org_id = None
    if team_id:
        org_id = (
            await conn.execute(select(team.c.organization_id).where(team.c.team_id == team_id))
        ).scalar()
    await conn.execute(
        insert(spend_logs).values(
            request_id=uuid4().hex,
            api_key=key["token"],
            user=key["user_id"],
            team_id=team_id,
            organization_id=org_id,
            model_group=model,
            startTime=start,
            endTime=end,
            spend=cost,
        )
    )
    await bump_spend(conn, key["token"], team_id, key["user_id"], org_id, cost)


async def bump_spend(
    conn: AsyncConnection,
    token_hash: str | None,
    team_id: str | None,
    user_id: str | None,
    org_id: str | None,
    amount: float,
) -> None:
    if token_hash:
        await conn.execute(
            update(token).where(token.c.token == token_hash).values(spend=token.c.spend + amount)
        )
    if team_id and user_id:
        await conn.execute(
            update(team_member)
            .where(team_member.c.team_id == team_id, team_member.c.user_id == user_id)
            .values(spend=team_member.c.spend + amount)
        )
    if team_id:
        await conn.execute(
            update(team).where(team.c.team_id == team_id).values(spend=team.c.spend + amount)
        )
    if org_id:
        await conn.execute(
            update(organization)
            .where(organization.c.organization_id == org_id)
            .values(spend=organization.c.spend + amount)
        )


router = APIRouter()


@router.post("/chat/completions")
@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    state: FakeState = request.app.state.fake
    if state.outage:
        raise HTTPException(503, detail={"error": "service unavailable (simulated outage)"})
    body = await request.json()
    model = body.get("model", "")
    try:
        async with state.engine.connect() as conn:
            key = await _authenticate(conn, request.headers.get("authorization", ""))
            tok = key["token"]
            limit = key["max_parallel_requests"]
            # R7: the check-and-increment has no await in between, so it is atomic per process.
            if limit is not None and state.in_flight.get(tok, 0) >= limit:
                return _error(429, "max_parallel_requests", "Max parallel request limit reached")
            state.in_flight[tok] = state.in_flight.get(tok, 0) + 1
            try:
                await _check_budgets(conn, key, model, state.now())
                start = state.now()
                await asyncio.sleep(state.latency_ms / 1000)
                end = state.now()
                async with state.engine.begin() as wconn:
                    await _charge(wconn, key, model, state.cost_per_request, start, end)
            finally:
                state.in_flight[tok] -= 1
    except _Reject as r:
        return r.resp
    return {
        "id": f"chatcmpl-{uuid4().hex}",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
