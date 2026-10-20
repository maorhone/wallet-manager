"""Fake LiteLLM storage: Core tables with the ERD names/columns (quoted identifiers)."""

from collections.abc import Awaitable, Callable

from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    DateTime,
    Float,
    Identity,
    Integer,
    MetaData,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import truncate_with_retry

metadata = MetaData()

budget = Table(
    "LiteLLM_BudgetTable",
    metadata,
    Column("budget_id", Text, primary_key=True),
    Column("max_budget", Float),
    Column("budget_duration", Text),
    Column("model_max_budget", JSONB),
    Column("temp_budget_increase", Float),
    Column("temp_budget_expiry", DateTime(timezone=True)),
    Column("tpm_limit", Integer),
    Column("rpm_limit", Integer),
)
organization = Table(
    "LiteLLM_OrganizationTable",
    metadata,
    Column("organization_id", Text, primary_key=True),
    Column("organization_alias", Text),
    Column("budget_id", Text),
    Column("models", ARRAY(Text)),
    Column("spend", Float, default=0),
    Column("metadata", JSONB),
)
org_member = Table(
    "LiteLLM_OrganizationMembership",
    metadata,
    Column("user_id", Text, primary_key=True),
    Column("organization_id", Text, primary_key=True),
    Column("user_role", Text),
)
team = Table(
    "LiteLLM_TeamTable",
    metadata,
    Column("team_id", Text, primary_key=True),
    Column("team_alias", Text),
    Column("organization_id", Text),
    Column("max_budget", Float),
    Column("spend", Float, default=0),
    Column("budget_duration", Text),
    Column("model_max_budget", JSONB),
    Column("tpm_limit", Integer),
    Column("max_parallel_requests", Integer),
    Column("members_with_roles", JSONB),
    Column("metadata", JSONB),
)
team_member = Table(
    "LiteLLM_TeamMembership",
    metadata,
    Column("user_id", Text, primary_key=True),
    Column("team_id", Text, primary_key=True),
    Column("budget_id", Text),
    Column("spend", Float, default=0),
)
user = Table(
    "LiteLLM_UserTable",
    metadata,
    Column("user_id", Text, primary_key=True),
    Column("sso_user_id", Text),
    Column("user_email", Text),
    Column("organization_id", Text),
    Column("teams", ARRAY(Text)),
)
token = Table(
    "LiteLLM_VerificationToken",
    metadata,
    Column("token", Text, primary_key=True),
    Column("key_alias", Text),
    Column("user_id", Text),
    Column("team_id", Text),
    Column("max_budget", Float),
    Column("spend", Float, default=0),
    Column("max_parallel_requests", Integer),
    Column("rpm_limit", Integer),
    Column("tpm_limit", Integer),
    Column("metadata", JSONB),
    Column("model_max_budget", JSONB),
    Column("blocked", Boolean, default=False),
    Column("organization_id", Text),
)
jwt_mapping = Table(
    "LiteLLM_JWTKeyMapping",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("jwt_issuer", Text),
    Column("jwt_claim_name", Text),
    Column("jwt_claim_value", Text),
    Column("token", Text),
    Column("is_active", Boolean, nullable=False, default=True, server_default="true"),
)
spend_logs = Table(
    "LiteLLM_SpendLogs",
    metadata,
    Column("request_id", Text, primary_key=True),
    Column("api_key", Text),
    Column("user", Text),
    Column("team_id", Text),
    Column("organization_id", Text),
    Column("model_group", Text),
    Column("startTime", DateTime(timezone=True)),
    Column("endTime", DateTime(timezone=True)),
    Column("spend", Float),
)


async def init_db(engine: AsyncEngine, ro_role: str = "litellm_ro") -> None:
    """Create the tables and the read-only role (R19) with SELECT on current and future tables."""
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        db = await conn.scalar(text("SELECT quote_ident(current_database())"))
        await conn.execute(
            text(
                f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ro_role}') "
                f"THEN CREATE ROLE {ro_role} LOGIN; END IF; END $$"
            )
        )
        await conn.execute(text(f"GRANT CONNECT ON DATABASE {db} TO {ro_role}"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {ro_role}"))
        await conn.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ro_role}"))
        await conn.execute(
            text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {ro_role}")
        )


async def reset_db(
    engine: AsyncEngine, on_conflict: Callable[[AsyncEngine], Awaitable[None]] | None = None
) -> None:
    names = ", ".join(f'"{t.name}"' for t in metadata.sorted_tables)
    await truncate_with_retry(engine, f"TRUNCATE {names} RESTART IDENTITY", on_conflict=on_conflict)
