"""Typed request/response models for the LiteLLM management API (SPEC §10, §8.1).

Requests forbid extra fields and are dumped with ``exclude_none=True``. Responses ignore
unknown fields so a newer LiteLLM does not break parsing.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Req(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def payload(self) -> dict:
        return self.model_dump(mode="json", exclude_none=True)


class _Resp(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ModelBudget(_Req):
    """Value of a ``model_max_budget`` map (shape re-verified in Phase 0, V1)."""

    budget_limit: float
    time_period: str


class Member(_Req):
    user_id: str
    role: Literal["user", "admin", "org_admin"] = "user"


# --- requests ---------------------------------------------------------------------------------


class OrganizationNew(_Req):
    organization_id: str
    organization_alias: str
    max_budget: float | None = None
    budget_duration: str | None = None
    models: list[str] | None = None


class OrganizationUpdate(_Req):
    organization_id: str
    organization_alias: str | None = None
    max_budget: float | None = None
    budget_duration: str | None = None
    models: list[str] | None = None


class OrganizationMemberAdd(_Req):
    organization_id: str
    member: Member


class _TeamFields(_Req):
    team_alias: str | None = None
    organization_id: str | None = None
    max_budget: float | None = None
    budget_duration: str | None = None
    model_max_budget: dict[str, ModelBudget] | None = None
    tpm_limit: int | None = None
    metadata: dict | None = None  # holds model_tpm_limit (§8.1)
    team_member_budget: float | None = None
    team_member_budget_duration: str | None = None
    team_member_model_max_budget: dict[str, ModelBudget] | None = None


class TeamNew(_TeamFields):
    team_id: str


class TeamUpdate(_TeamFields):
    team_id: str

    def payload(self) -> dict:
        body = super().payload()
        for name in ("tpm_limit", "team_member_model_max_budget"):
            if name in self.model_fields_set:
                body[name] = self.model_dump(mode="json")[name]
        return body


class TeamMemberAdd(_Req):
    team_id: str
    member: Member
    max_budget_in_team: float | None = None


class TeamMemberUpdate(_Req):
    team_id: str
    user_id: str
    role: Literal["user", "admin"] | None = None
    max_budget_in_team: float
    budget_duration: str
    temp_budget_increase: float | None = None
    temp_budget_expiry: datetime | None = None
    model_max_budget: dict[str, ModelBudget] | None = None
    tpm_limit: int | None = None
    rpm_limit: int | None = None

    @model_validator(mode="after")
    def _temp_fields_together(self):
        """Both or neither; ``0`` with no expiry clears the increase (FR-P4)."""
        increase, expiry = self.temp_budget_increase, self.temp_budget_expiry
        if (increase is None) != (expiry is None) and (expiry is not None or increase != 0):
            raise ValueError("temp_budget_increase and temp_budget_expiry go together")
        return self

    def payload(self) -> dict:
        body = super().payload()
        if self.temp_budget_increase is not None:
            body["temp_budget_expiry"] = body.get("temp_budget_expiry")  # always both keys
        return body


class TeamMemberDelete(_Req):
    team_id: str
    user_id: str


class UserNew(_Req):
    user_id: str
    sso_user_id: str
    user_email: str


class KeyGenerate(_Req):
    # No model_max_budget on purpose: a key-level map would override the group pot (DR3/R6).
    user_id: str
    team_id: str | None = None
    key_alias: str | None = None
    max_parallel_requests: int | None = None
    rpm_limit: int | None = None
    model_tpm_limit: dict[str, int] | None = None


class KeyUpdate(_Req):
    key: str  # token hash or plaintext key
    team_id: str | None = None
    key_alias: str | None = None
    max_parallel_requests: int | None = None
    rpm_limit: int | None = None
    model_tpm_limit: dict[str, int] | None = None

    def payload(self) -> dict:
        body = super().payload()
        if "model_tpm_limit" in self.model_fields_set:
            body["model_tpm_limit"] = self.model_tpm_limit
        return body


class KeyBlock(_Req):
    key: str


class KeyDelete(_Req):
    keys: list[str]  # token hashes or plaintext keys (V7)


class UserDelete(_Req):
    user_ids: list[str]


class JwtKeyMappingNew(_Req):
    jwt_issuer: str
    jwt_claim_name: str
    jwt_claim_value: str
    token: str


class JwtKeyMappingUpdate(_Req):
    jwt_issuer: str
    jwt_claim_name: str
    jwt_claim_value: str
    is_active: bool  # kick sets it false, restore true (V6)


class JwtKeyMappingDelete(_Req):
    jwt_issuer: str
    jwt_claim_name: str
    jwt_claim_value: str


# --- responses --------------------------------------------------------------------------------


class HealthReadiness(_Resp):
    status: str
    db: str | None = None
    cache: str | None = None
    litellm_version: str | None = None


class BudgetRecord(_Resp):
    max_budget: float | None = None
    budget_duration: str | None = None
    model_max_budget: dict | None = None
    temp_budget_increase: float | None = None
    temp_budget_expiry: datetime | None = None
    tpm_limit: int | None = None
    rpm_limit: int | None = None


class OrganizationMemberRecord(_Resp):
    user_id: str
    user_role: str


class OrganizationInfo(_Resp):
    organization_id: str
    organization_alias: str | None = None
    budget_id: str | None = None
    models: list[str] = Field(default_factory=list)
    spend: float = 0
    metadata: dict = Field(default_factory=dict)
    litellm_budget_table: BudgetRecord = Field(default_factory=BudgetRecord)
    members: list[OrganizationMemberRecord] = Field(default_factory=list)


class TeamRecord(_Resp):
    team_id: str
    team_alias: str | None = None
    organization_id: str | None = None
    max_budget: float | None = None
    spend: float = 0
    budget_duration: str | None = None
    model_max_budget: dict | None = None
    tpm_limit: int | None = None
    max_parallel_requests: int | None = None
    members_with_roles: list[dict] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class TeamMembershipRecord(_Resp):
    user_id: str
    team_id: str
    spend: float = 0
    litellm_budget_table: BudgetRecord | None = None


class TeamInfo(_Resp):
    team_id: str
    team_info: TeamRecord
    team_memberships: list[TeamMembershipRecord] = Field(default_factory=list)


class UserRecord(_Resp):
    user_id: str
    sso_user_id: str | None = None
    user_email: str | None = None
    organization_id: str | None = None
    teams: list[str] = Field(default_factory=list)


class KeyGenerateResponse(_Resp):
    key: str  # plaintext, returned once; never log or store (NFR5)
    token: str  # sha256 hash


class KeyRecord(_Resp):
    token: str
    key_alias: str | None = None
    user_id: str | None = None
    team_id: str | None = None
    max_parallel_requests: int | None = None
    rpm_limit: int | None = None
    blocked: bool = False
    metadata: dict = Field(default_factory=dict)


class KeyListResponse(_Resp):
    keys: list[KeyRecord] = Field(default_factory=list)


class JwtKeyMappingRecord(_Resp):
    id: int
    jwt_issuer: str
    jwt_claim_name: str
    jwt_claim_value: str
    token: str
    is_active: bool = True


class JwtKeyMappingList(_Resp):
    mappings: list[JwtKeyMappingRecord]
