"""ORM for SPEC §8.2. Table and column names are verbatim from the spec."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import DateTime


class Base(DeclarativeBase):
    pass


ID = {"primary_key": True, "autoincrement": True}
TS = DateTime(timezone=True)
ALLOC = Numeric(18, 2)
SPEND = Numeric(18, 4)


def _in(col: str, *values: str) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def _created() -> Mapped[datetime]:
    return mapped_column(TS, server_default=func.now())


class Department(Base):
    __tablename__ = "department"
    __table_args__ = (Index("uq_department_ad_group_dn", "ad_group_dn", unique=True),)
    id: Mapped[int] = mapped_column(BigInteger, **ID)
    litellm_org_id: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    ad_group_dn: Mapped[str | None] = mapped_column(Text)
    budget: Mapped[Decimal] = mapped_column(ALLOC, server_default="0")
    budget_period: Mapped[str] = mapped_column(Text, server_default="1mo")
    pmo_pool: Mapped[Decimal] = mapped_column(ALLOC, server_default="0")
    reserve: Mapped[Decimal] = mapped_column(ALLOC, server_default="0")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class DepartmentModelAllowance(Base):
    __tablename__ = "department_model_allowance"
    department_id: Mapped[int] = mapped_column(ForeignKey("department.id"), primary_key=True)
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    amount: Mapped[Decimal] = mapped_column(ALLOC)


class Grp(Base):
    __tablename__ = "grp"
    __table_args__ = (
        Index("uq_grp_ad_group_dn", "ad_group_dn", unique=True),
        Index(
            "uq_grp_department_staff",
            "department_id",
            unique=True,
            postgresql_where=text("kind = 'department_staff'"),
        ),
        CheckConstraint(_in("kind", "regular", "department_staff"), name="ck_grp_kind"),
        CheckConstraint(
            _in("status", "active", "pending_dept_move", "archived"), name="ck_grp_status"
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, **ID)
    litellm_team_id: Mapped[str] = mapped_column(Text, unique=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("department.id"))
    name: Mapped[str] = mapped_column(Text)
    ad_group_dn: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, server_default="regular")
    base_budget: Mapped[Decimal] = mapped_column(ALLOC, server_default="0")
    default_member_cap: Mapped[Decimal] = mapped_column(ALLOC, server_default="0")
    overalloc_factor: Mapped[Decimal] = mapped_column(Numeric, server_default="1.0")
    tpm_limit: Mapped[int | None] = mapped_column(BigInteger)
    model_tpm_limits: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default="active")


class GroupModelPot(Base):
    __tablename__ = "group_model_pot"
    group_id: Mapped[int] = mapped_column(ForeignKey("grp.id"), primary_key=True)
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    amount: Mapped[Decimal] = mapped_column(ALLOC)


class Employee(Base):
    __tablename__ = "employee"
    __table_args__ = (
        CheckConstraint(
            _in("status", "active", "blocked", "left", "pending_placement"),
            name="ck_employee_status",
        ),
        CheckConstraint(
            "pending_reason IS NULL OR "
            + _in(
                "pending_reason", "no_budget_group", "multiple_budget_groups", "not_in_directory"
            ),
            name="ck_employee_pending_reason",
        ),
    )
    upn: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    litellm_user_id: Mapped[str | None] = mapped_column(Text)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("grp.id"))
    base_cap: Mapped[Decimal] = mapped_column(ALLOC, server_default="0")
    status: Mapped[str] = mapped_column(Text, server_default="active")
    key_token_hash: Mapped[str | None] = mapped_column(Text)
    max_parallel: Mapped[int | None] = mapped_column(Integer)
    rpm_limit: Mapped[int | None] = mapped_column(Integer)
    model_tpm_limits: Mapped[dict | None] = mapped_column(JSONB)
    left_at: Mapped[datetime | None] = mapped_column(TS)
    # §9.5: set while status is pending_placement, cleared on place
    pending_department_id: Mapped[int | None] = mapped_column(ForeignKey("department.id"))
    pending_reason: Mapped[str | None] = mapped_column(Text)
    pending_since: Mapped[datetime | None] = mapped_column(TS)


class EmployeeModelCap(Base):
    __tablename__ = "employee_model_cap"
    upn: Mapped[str] = mapped_column(ForeignKey("employee.upn"), primary_key=True)
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    amount: Mapped[Decimal] = mapped_column(ALLOC)


class Grant(Base):
    __tablename__ = "grant"
    __table_args__ = (
        CheckConstraint(_in("status", "pending", "active", "closed"), name="ck_grant_status"),
    )
    id: Mapped[int] = mapped_column(BigInteger, **ID)
    department_id: Mapped[int] = mapped_column(ForeignKey("department.id"))
    group_id: Mapped[int | None] = mapped_column(ForeignKey("grp.id"))
    amount: Mapped[Decimal] = mapped_column(ALLOC)
    model: Mapped[str | None] = mapped_column(Text)
    project_ref: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[datetime | None] = mapped_column(TS)
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    created_by: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(TS)
    used: Mapped[Decimal] = mapped_column(SPEND, server_default="0")
    returned: Mapped[Decimal] = mapped_column(SPEND, server_default="0")


class GrantShare(Base):
    __tablename__ = "grant_share"
    __table_args__ = (
        CheckConstraint(_in("status", "active", "closed"), name="ck_grant_share_status"),
        CheckConstraint(
            _in("closed_reason", "expiry", "move", "manual"), name="ck_grant_share_closed_reason"
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, **ID)
    grant_id: Mapped[int] = mapped_column(ForeignKey("grant.id"))
    upn: Mapped[str] = mapped_column(ForeignKey("employee.upn"))
    amount: Mapped[Decimal] = mapped_column(ALLOC)
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    used: Mapped[Decimal] = mapped_column(SPEND, server_default="0")
    returned: Mapped[Decimal] = mapped_column(SPEND, server_default="0")
    closed_reason: Mapped[str | None] = mapped_column(Text)


class ServiceKey(Base):
    __tablename__ = "service_key"
    id: Mapped[int] = mapped_column(BigInteger, **ID)
    owner_kind: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    litellm_token_hash: Mapped[str | None] = mapped_column(Text)
    alias: Mapped[str | None] = mapped_column(Text)
    max_budget: Mapped[Decimal | None] = mapped_column(ALLOC)
    max_parallel: Mapped[int | None] = mapped_column(Integer)
    rpm_limit: Mapped[int | None] = mapped_column(Integer)
    models: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    status: Mapped[str] = mapped_column(Text, server_default="active")


class Policy(Base):
    __tablename__ = "policy"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict | list | int | str | None] = mapped_column(JSONB)


class RoleBinding(Base):
    __tablename__ = "role_binding"
    # §8.2 lists no key; scope_id is null for platform-wide roles, so no DB PK.
    __mapper_args__ = {"primary_key": ["ad_group_dn", "role", "scope_kind", "scope_id"]}
    ad_group_dn: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    scope_kind: Mapped[str] = mapped_column(Text)
    scope_id: Mapped[int | None] = mapped_column(BigInteger)


class LitellmWrite(Base):
    __tablename__ = "litellm_write"
    __table_args__ = (Index("ix_litellm_write_status_id", "status", "id"),)
    id: Mapped[int] = mapped_column(BigInteger, **ID)
    entity: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str] = mapped_column(Text)
    endpoint: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    depends_on: Mapped[int | None] = mapped_column(ForeignKey("litellm_write.id"))  # FR-RC3, §9.3


class SpendSnapshot(Base):
    __tablename__ = "spend_snapshot"
    __table_args__ = (Index("ix_spend_snapshot_scope", "scope_kind", "scope_id", "period"),)
    # §8.2 lists no key and model is nullable: map the dimensions as identity, no DB PK.
    __mapper_args__ = {"primary_key": ["scope_kind", "scope_id", "period", "model"]}
    scope_kind: Mapped[str] = mapped_column(Text)
    scope_id: Mapped[str] = mapped_column(Text)
    period: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    spend: Mapped[Decimal] = mapped_column(SPEND)
    taken_at: Mapped[datetime] = _created()


class Notification(Base):
    __tablename__ = "notification"
    id: Mapped[int] = mapped_column(BigInteger, **ID)
    upn: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    scope_kind: Mapped[str | None] = mapped_column(Text)
    scope_id: Mapped[int | None] = mapped_column(BigInteger)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()
    read_at: Mapped[datetime | None] = mapped_column(TS)


class NotificationPref(Base):
    __tablename__ = "notification_pref"
    upn: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    muted: Mapped[bool] = mapped_column(Boolean, server_default="false")


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_entity", "entity", "entity_id", "at"),)
    id: Mapped[int] = mapped_column(BigInteger, **ID)
    at: Mapped[datetime] = _created()
    actor_upn: Mapped[str | None] = mapped_column(Text)
    actor_role: Mapped[str | None] = mapped_column(Text)
    entity: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str] = mapped_column(Text)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
