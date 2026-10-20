"""Desired-state builders (SPEC §8.1), evaluated at apply time so retries send the latest values.

``build(session, settings, endpoint, entity_id)`` returns the typed LiteLLM request for the
outbox row's endpoint. ``DesiredUnavailable`` means the state it needs does not exist (yet).
"""

from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.litellm_client import (
    JwtKeyMappingNew,
    JwtKeyMappingUpdate,
    KeyBlock,
    KeyGenerate,
    KeyUpdate,
    Member,
    ModelBudget,
    OrganizationNew,
    OrganizationUpdate,
    TeamMemberAdd,
    TeamMemberUpdate,
    TeamNew,
    TeamUpdate,
    UserNew,
)
from app.models import (
    Department,
    Employee,
    EmployeeModelCap,
    Grant,
    GrantShare,
    GroupModelPot,
    Grp,
    Policy,
)
from app.services.spend import month_bounds


class DesiredUnavailable(Exception):
    pass


async def _get(session: AsyncSession, model, key):
    row = await session.get(model, key if model is Employee else int(key))
    if row is None:
        raise DesiredUnavailable(f"{model.__name__} {key} not found")
    return row


def _models(amounts: dict[str, Decimal], period: str) -> dict[str, ModelBudget]:
    return {m: ModelBudget(budget_limit=float(a), time_period=period) for m, a in amounts.items()}


async def _policy(session: AsyncSession, key: str, default=None):
    row = await session.get(Policy, key)
    return row.value if row else default


async def _org_fields(session: AsyncSession, entity_id: str) -> dict:
    d = await _get(session, Department, entity_id)
    return {
        "organization_id": d.litellm_org_id, "organization_alias": d.name,
        "max_budget": float(d.budget), "budget_duration": d.budget_period,
    }  # fmt: skip


def contributing_grants(timezone: str = "Asia/Jerusalem", period: str | None = None):
    """Closed spending still occupies the team's ceiling until the local month resets."""
    if period is not None:
        start, end = month_bounds(period, ZoneInfo(timezone))
        closed = (Grant.closed_at >= start) & (Grant.closed_at < end)
    else:
        start = func.date_trunc("month", func.timezone(timezone, func.now())).op("AT TIME ZONE")(
            timezone
        )
        closed = (Grant.closed_at >= start) & (Grant.closed_at <= func.now())
    return (Grant.status == "active") | ((Grant.status == "closed") & closed)


async def _team_fields(session: AsyncSession, entity_id: str, timezone: str) -> dict:
    g = await _get(session, Grp, entity_id)
    dept = await session.get(Department, g.department_id)
    period = dept.budget_period
    active = (Grant.group_id == g.id, contributing_grants(timezone))
    effective = Grant.amount - Grant.returned
    grants = await session.scalar(select(func.coalesce(func.sum(effective), 0)).where(*active))
    pots = {
        r.model: r.amount
        for r in await session.scalars(select(GroupModelPot).where(GroupModelPot.group_id == g.id))
    }
    for r in await session.execute(
        select(Grant.model, effective.label("amount")).where(*active, Grant.model.is_not(None))
    ):
        pots[r.model] = pots.get(r.model, Decimal(0)) + r.amount
    defaults = await _policy(session, f"member_defaults.group.{g.id}", {})
    fields = {
        "team_alias": g.name, "organization_id": dept.litellm_org_id,
        "max_budget": float(g.base_budget + grants), "budget_duration": period,
        "model_max_budget": _models({m: a for m, a in pots.items() if a}, period),
        "tpm_limit": g.tpm_limit,
        "metadata": {"model_tpm_limit": g.model_tpm_limits} if g.model_tpm_limits else None,
        "team_member_budget": float(g.default_member_cap), "team_member_budget_duration": period,
        "team_member_model_max_budget": _models(
            {m: Decimal(str(a)) for m, a in (defaults or {}).get("model_caps", {}).items()}, period
        ) or None,
    }  # fmt: skip
    return {"team_id": g.litellm_team_id, **fields}


async def _employee(session: AsyncSession, upn: str) -> tuple[Employee, Grp, Department]:
    e = await _get(session, Employee, upn)
    g = await session.get(Grp, e.group_id) if e.group_id else None
    if g is None:
        raise DesiredUnavailable(f"{upn} has no group")
    return e, g, await session.get(Department, g.department_id)


async def _key_limits(session: AsyncSession, e: Employee) -> dict:
    conc = await _policy(session, "concurrency", {})
    rpm = await _policy(session, "rpm", {})
    return {
        "max_parallel_requests": e.max_parallel or conc.get("default"),
        "rpm_limit": e.rpm_limit or rpm.get("default"),
        "model_tpm_limit": e.model_tpm_limits or None,
    }


async def build(session: AsyncSession, settings: Settings, endpoint: str, entity_id: str):
    match endpoint:
        case "/organization/new":
            return OrganizationNew(**await _org_fields(session, entity_id))
        case "/organization/update":
            return OrganizationUpdate(**await _org_fields(session, entity_id))
        case "/team/new":
            return TeamNew(**await _team_fields(session, entity_id, settings.app_timezone))
        case "/team/update":
            return TeamUpdate(**await _team_fields(session, entity_id, settings.app_timezone))
        case "/user/new":
            e = await _get(session, Employee, entity_id)
            return UserNew(user_id=e.upn, sso_user_id=e.upn, user_email=e.upn)
        case "/team/member_add":
            e, g, _ = await _employee(session, entity_id)
            return TeamMemberAdd(
                team_id=g.litellm_team_id, member=Member(user_id=e.upn),
                max_budget_in_team=float(e.base_cap),
            )  # fmt: skip
        case "/team/member_update":
            e, g, d = await _employee(session, entity_id)
            caps = {
                c.model: c.amount
                for c in await session.scalars(
                    select(EmployeeModelCap).where(EmployeeModelCap.upn == e.upn)
                )
            }
            shares = await session.execute(
                select(GrantShare.amount, GrantShare.expires_at, Grant.model)
                .join(Grant, Grant.id == GrantShare.grant_id)
                .where(GrantShare.upn == e.upn, GrantShare.status == "active")
            )
            increase, expiry = Decimal(0), None
            for amount, expires_at, model in shares:  # FR-P4 / FR-P5
                increase += amount
                expiry = expires_at if expiry is None else min(expiry, expires_at)
                if model:
                    caps[model] = caps.get(model, Decimal(0)) + amount
            return TeamMemberUpdate(
                team_id=g.litellm_team_id, user_id=e.upn, max_budget_in_team=float(e.base_cap),
                budget_duration=d.budget_period, temp_budget_increase=float(increase),
                temp_budget_expiry=expiry, model_max_budget=_models(caps, d.budget_period) or None,
            )  # fmt: skip
        case "/key/generate":
            e, g, _ = await _employee(session, entity_id)
            return KeyGenerate(
                user_id=e.upn, team_id=g.litellm_team_id, **await _key_limits(session, e)
            )
        case "/key/update":
            e, g, _ = await _employee(session, entity_id)
            if not e.key_token_hash:
                raise DesiredUnavailable(f"{e.upn} has no key yet")
            return KeyUpdate(
                key=e.key_token_hash, team_id=g.litellm_team_id, **await _key_limits(session, e)
            )
        case "/key/block" | "/key/unblock":
            e = await _get(session, Employee, entity_id)
            if not e.key_token_hash:
                raise DesiredUnavailable(f"{e.upn} has no key yet")
            return KeyBlock(key=e.key_token_hash)
        case "/jwt/key/mapping/new":
            e = await _get(session, Employee, entity_id)
            if not e.key_token_hash:
                raise DesiredUnavailable(f"{e.upn} has no key yet")
            return JwtKeyMappingNew(
                jwt_issuer=settings.jwt_issuer, jwt_claim_name="upn", jwt_claim_value=e.upn,
                token=e.key_token_hash,
            )  # fmt: skip
        case "/jwt/key/mapping/update":
            e = await _get(session, Employee, entity_id)
            return JwtKeyMappingUpdate(
                jwt_issuer=settings.jwt_issuer, jwt_claim_name="upn", jwt_claim_value=e.upn,
                is_active=e.status == "active",
            )  # fmt: skip
    raise DesiredUnavailable(f"no builder for {endpoint}")
