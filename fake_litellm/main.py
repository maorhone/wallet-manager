"""Fake LiteLLM management API (section-10 endpoints), parsed with the client's own models."""

import hashlib
import os
import secrets
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.config import Settings
from app.db import make_engine
from app.litellm_client import models as m

from . import db
from .controls import router as controls_router
from .db import budget, jwt_mapping, org_member, organization, team, team_member, token, user
from .state import FakeState


def _err(status: int, msg: str) -> HTTPException:
    return HTTPException(status, detail={"error": msg})


async def check_outage(request: Request) -> None:
    if request.app.state.fake.outage:
        raise _err(503, "service unavailable (simulated outage)")


async def guard(request: Request) -> None:
    await check_outage(request)
    state: FakeState = request.app.state.fake
    if request.headers.get("authorization") != f"Bearer {state.master_key}":
        raise _err(401, "Authentication Error, invalid key")


async def _one(conn: AsyncConnection, table, col, value, what: str):
    row = (await conn.execute(select(table).where(table.c[col] == value))).mappings().first()
    if row is None:
        raise _err(404, f"{what} {value} not found")
    return dict(row)


async def _must_not_exist(conn: AsyncConnection, table, col, value, what: str):
    if (await conn.execute(select(table.c[col]).where(table.c[col] == value))).first():
        raise _err(400, f"{what} {value} already exists")


# --- organizations ----------------------------------------------------------------------------


async def _org_info(conn: AsyncConnection, org_id: str) -> m.OrganizationInfo:
    org = await _one(conn, organization, "organization_id", org_id, "organization")
    bud = (
        await conn.execute(select(budget).where(budget.c.budget_id == org["budget_id"]))
    ).mappings().first()  # fmt: skip
    members = (
        await conn.execute(select(org_member).where(org_member.c.organization_id == org_id))
    ).mappings()
    return m.OrganizationInfo(
        **org,
        litellm_budget_table=m.BudgetRecord(**(bud or {})),
        members=[m.OrganizationMemberRecord(**r) for r in members],
    )


def _org_router() -> APIRouter:
    r = APIRouter()

    @r.post("/organization/new")
    async def organization_new(body: m.OrganizationNew, request: Request):
        async with request.app.state.fake.engine.begin() as conn:
            await _must_not_exist(
                conn, organization, "organization_id", body.organization_id, "org"
            )
            budget_id = uuid4().hex
            await conn.execute(
                insert(budget).values(
                    budget_id=budget_id,
                    max_budget=body.max_budget,
                    budget_duration=body.budget_duration,
                )
            )
            await conn.execute(
                insert(organization).values(
                    organization_id=body.organization_id,
                    organization_alias=body.organization_alias,
                    budget_id=budget_id,
                    models=body.models or [],
                    spend=0,
                    metadata={},
                )
            )
            return await _org_info(conn, body.organization_id)

    @r.patch("/organization/update")
    async def organization_update(body: m.OrganizationUpdate, request: Request):
        async with request.app.state.fake.engine.begin() as conn:
            org = await _one(conn, organization, "organization_id", body.organization_id, "org")
            bud_vals = {
                k: v
                for k, v in (
                    ("max_budget", body.max_budget),
                    ("budget_duration", body.budget_duration),
                )
                if v is not None
            }
            if bud_vals:
                await conn.execute(
                    update(budget).where(budget.c.budget_id == org["budget_id"]).values(**bud_vals)
                )
            org_vals = {
                k: v
                for k, v in (
                    ("organization_alias", body.organization_alias),
                    ("models", body.models),
                )
                if v is not None
            }
            if org_vals:
                await conn.execute(
                    update(organization)
                    .where(organization.c.organization_id == body.organization_id)
                    .values(**org_vals)
                )
            return await _org_info(conn, body.organization_id)

    @r.post("/organization/member_add")
    async def organization_member_add(body: m.OrganizationMemberAdd, request: Request):
        async with request.app.state.fake.engine.begin() as conn:
            await _one(conn, organization, "organization_id", body.organization_id, "org")
            await conn.execute(
                insert(org_member).values(
                    user_id=body.member.user_id,
                    organization_id=body.organization_id,
                    user_role=body.member.role,
                )
            )
            return await _org_info(conn, body.organization_id)

    @r.get("/organization/info")
    async def organization_info(organization_id: str, request: Request):
        async with request.app.state.fake.engine.connect() as conn:
            return await _org_info(conn, organization_id)

    return r


# --- teams ------------------------------------------------------------------------------------

_TEAM_COLS = (
    "team_alias",
    "organization_id",
    "max_budget",
    "budget_duration",
    "model_max_budget",
    "tpm_limit",
)
_TEAM_META = ("team_member_budget", "team_member_budget_duration", "team_member_model_max_budget")


def _team_values(body: m._Req) -> tuple[dict, dict]:
    data = body.model_dump(exclude_none=True)
    cols = {k: data[k] for k in _TEAM_COLS if k in data}
    meta = {**data.get("metadata", {}), **{k: data[k] for k in _TEAM_META if k in data}}
    return cols, meta


async def _team_info(conn: AsyncConnection, team_id: str) -> m.TeamInfo:
    row = await _one(conn, team, "team_id", team_id, "team")
    memberships = (
        await conn.execute(
            select(team_member, budget)
            .select_from(
                team_member.outerjoin(budget, budget.c.budget_id == team_member.c.budget_id)
            )
            .where(team_member.c.team_id == team_id)
            .order_by(team_member.c.user_id)
        )
    ).mappings()
    return m.TeamInfo(
        team_id=team_id,
        team_info=m.TeamRecord(**row),
        team_memberships=[
            m.TeamMembershipRecord(
                user_id=r["user_id"],
                team_id=team_id,
                spend=r["spend"] or 0,
                litellm_budget_table=m.BudgetRecord(**r) if r["budget_id"] else None,
            )
            for r in memberships
        ],
    )


async def _membership(conn: AsyncConnection, team_id: str, user_id: str) -> dict:
    row = (
        await conn.execute(
            select(team_member).where(
                team_member.c.team_id == team_id, team_member.c.user_id == user_id
            )
        )
    ).mappings().first()  # fmt: skip
    if row is None:
        raise _err(404, f"{user_id} is not a member of team {team_id}")
    return dict(row)


async def _set_roles(conn: AsyncConnection, team_row: dict, fn) -> None:
    roles = fn(list(team_row["members_with_roles"] or []))
    await conn.execute(
        update(team).where(team.c.team_id == team_row["team_id"]).values(members_with_roles=roles)
    )


def _team_router() -> APIRouter:
    r = APIRouter()

    @r.post("/team/new")
    async def team_new(body: m.TeamNew, request: Request):
        async with request.app.state.fake.engine.begin() as conn:
            await _must_not_exist(conn, team, "team_id", body.team_id, "team")
            if body.organization_id:
                await _one(conn, organization, "organization_id", body.organization_id, "org")
            cols, meta = _team_values(body)
            await conn.execute(
                insert(team).values(
                    team_id=body.team_id, spend=0, members_with_roles=[], metadata=meta, **cols
                )
            )
            return (await _team_info(conn, body.team_id)).team_info

    @r.post("/team/update")
    async def team_update(body: m.TeamUpdate, request: Request):
        async with request.app.state.fake.engine.begin() as conn:
            row = await _one(conn, team, "team_id", body.team_id, "team")
            cols, meta = _team_values(body)
            await conn.execute(
                update(team)
                .where(team.c.team_id == body.team_id)
                .values(metadata={**(row["metadata"] or {}), **meta}, **cols)
            )
            return (await _team_info(conn, body.team_id)).team_info

    @r.get("/team/info")
    async def team_info(team_id: str, request: Request):
        async with request.app.state.fake.engine.connect() as conn:
            return await _team_info(conn, team_id)

    @r.post("/team/member_add")
    async def team_member_add(body: m.TeamMemberAdd, request: Request):
        uid = body.member.user_id
        async with request.app.state.fake.engine.begin() as conn:
            row = await _one(conn, team, "team_id", body.team_id, "team")
            if any(x["user_id"] == uid for x in row["members_with_roles"] or []):
                raise _err(400, f"{uid} already in team {body.team_id}")
            budget_id = None
            if body.max_budget_in_team is not None:
                budget_id = uuid4().hex
                await conn.execute(
                    insert(budget).values(budget_id=budget_id, max_budget=body.max_budget_in_team)
                )
            await conn.execute(
                insert(team_member).values(
                    user_id=uid, team_id=body.team_id, budget_id=budget_id, spend=0
                )
            )
            await _set_roles(
                conn, row, lambda rs: [*rs, {"user_id": uid, "role": body.member.role}]
            )
            return await _team_info(conn, body.team_id)

    @r.post("/team/member_update")
    async def team_member_update(body: m.TeamMemberUpdate, request: Request):
        async with request.app.state.fake.engine.begin() as conn:
            row = await _one(conn, team, "team_id", body.team_id, "team")
            mem = await _membership(conn, body.team_id, body.user_id)
            vals = {
                "max_budget": body.max_budget_in_team,
                "budget_duration": body.budget_duration,
                "temp_budget_increase": body.temp_budget_increase,
                "temp_budget_expiry": body.temp_budget_expiry,
                "tpm_limit": body.tpm_limit,
                "rpm_limit": body.rpm_limit,
                "model_max_budget": body.model_dump(exclude_none=True).get("model_max_budget"),
            }
            if mem["budget_id"]:
                await conn.execute(
                    update(budget).where(budget.c.budget_id == mem["budget_id"]).values(**vals)
                )
            else:
                budget_id = uuid4().hex
                await conn.execute(insert(budget).values(budget_id=budget_id, **vals))
                await conn.execute(
                    update(team_member)
                    .where(
                        team_member.c.team_id == body.team_id,
                        team_member.c.user_id == body.user_id,
                    )
                    .values(budget_id=budget_id)
                )
            if body.role:
                await _set_roles(
                    conn,
                    row,
                    lambda rs: [
                        {**x, "role": body.role} if x["user_id"] == body.user_id else x for x in rs
                    ],
                )
            return await _team_info(conn, body.team_id)

    @r.post("/team/member_delete")
    async def team_member_delete(body: m.TeamMemberDelete, request: Request):
        async with request.app.state.fake.engine.begin() as conn:
            row = await _one(conn, team, "team_id", body.team_id, "team")
            mem = await _membership(conn, body.team_id, body.user_id)
            await conn.execute(
                delete(team_member).where(
                    team_member.c.team_id == body.team_id, team_member.c.user_id == body.user_id
                )
            )
            if mem["budget_id"]:
                await conn.execute(delete(budget).where(budget.c.budget_id == mem["budget_id"]))
            await _set_roles(conn, row, lambda rs: [x for x in rs if x["user_id"] != body.user_id])
            return await _team_info(conn, body.team_id)

    return r


# --- users, keys, JWT mapping -----------------------------------------------------------------


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def _token_hash(conn: AsyncConnection, key: str) -> str:
    """Accept either the token hash or the plaintext key."""
    for candidate in (key, _hash(key)):
        if (await conn.execute(select(token.c.token).where(token.c.token == candidate))).first():
            return candidate
    raise _err(404, "key not found")


def _key_router() -> APIRouter:
    r = APIRouter()

    @r.post("/user/new")
    async def user_new(body: m.UserNew, request: Request):
        async with request.app.state.fake.engine.begin() as conn:
            await _must_not_exist(conn, user, "user_id", body.user_id, "user")
            await conn.execute(insert(user).values(**body.payload(), teams=[]))
            return m.UserRecord(**await _one(conn, user, "user_id", body.user_id, "user"))

    @r.post("/key/generate")
    async def key_generate(body: m.KeyGenerate, request: Request):
        key = "sk-" + secrets.token_urlsafe(24)
        data = body.payload()
        tpm = data.pop("model_tpm_limit", None)
        async with request.app.state.fake.engine.begin() as conn:
            await conn.execute(
                insert(token).values(
                    token=_hash(key),  # only the hash is persisted
                    spend=0,
                    blocked=False,
                    metadata={"model_tpm_limit": tpm} if tpm else {},
                    **data,
                )
            )
        return m.KeyGenerateResponse(key=key, token=_hash(key))

    @r.post("/key/update")
    async def key_update(body: m.KeyUpdate, request: Request):
        data = body.payload()
        key, tpm = data.pop("key"), data.pop("model_tpm_limit", None)
        async with request.app.state.fake.engine.begin() as conn:
            tok = await _token_hash(conn, key)
            if tpm:
                row = await _one(conn, token, "token", tok, "key")
                data["metadata"] = {**(row["metadata"] or {}), "model_tpm_limit": tpm}
            if data:
                await conn.execute(update(token).where(token.c.token == tok).values(**data))
        return {"status": "success"}

    def _block(blocked: bool):
        async def handler(body: m.KeyBlock, request: Request):
            async with request.app.state.fake.engine.begin() as conn:
                tok = await _token_hash(conn, body.key)
                await conn.execute(
                    update(token).where(token.c.token == tok).values(blocked=blocked)
                )
            return {"status": "success"}

        return handler

    r.post("/key/block")(_block(True))
    r.post("/key/unblock")(_block(False))

    @r.get("/key/list")
    async def key_list(user_id: str, request: Request):
        async with request.app.state.fake.engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(token).where(token.c.user_id == user_id).order_by(token.c.token)
                )
            ).mappings()
            return m.KeyListResponse(keys=[m.KeyRecord(**row) for row in rows])

    @r.post("/jwt/key/mapping/new")
    async def jwt_mapping_new(body: m.JwtKeyMappingNew, request: Request):
        async with request.app.state.fake.engine.begin() as conn:
            res = await conn.execute(
                insert(jwt_mapping).values(**body.payload()).returning(jwt_mapping.c.id)
            )
            return m.JwtKeyMappingRecord(id=res.scalar_one(), **body.payload())

    return r


def create_app(
    engine: AsyncEngine,
    master_key: str = "FAKE_MASTER_KEY",
    test_controls: bool | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await db.init_db(engine)
        yield

    app = FastAPI(title="Fake LiteLLM", lifespan=lifespan)
    app.state.fake = FakeState(engine=engine, master_key=master_key)

    @app.get("/health/liveliness", dependencies=[Depends(check_outage)])  # unauthenticated
    async def liveliness():
        return "I'm alive!"

    for router in (_org_router(), _team_router(), _key_router()):
        app.include_router(router, dependencies=[Depends(guard)])
    if test_controls is None:
        test_controls = os.environ.get("FAKE_TEST_CONTROLS", "1") == "1"
    if test_controls:
        app.include_router(controls_router)
    return app


app = create_app(
    make_engine(
        Settings().litellm_database_url or "postgresql://localhost/litellm_fake",
    ),
    os.environ.get("FAKE_MASTER_KEY", "FAKE_MASTER_KEY"),
)
