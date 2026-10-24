from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import Money

# §11.1 PUT /policies: a partial map of policy keys, raw (not a strict schema) so an unknown
# or ``member_defaults.*`` key can be reported with ``details.key`` instead of a generic
# FastAPI validation error.
PoliciesIn = dict[str, Any]


class DepartmentCreateIn(BaseModel):
    organization_id: int = Field(default=1, gt=0)
    name: str
    litellm_org_id: str
    ad_group_dn: str | None = None
    budget: Money
    budget_period: str = Field(default="1mo", pattern=r"^\d+(s|m|h|d|mo)$")


class GroupCreateIn(BaseModel):
    name: str
    litellm_team_id: str
    ad_group_dn: str | None = None
    kind: Literal["regular", "department_staff"] = "regular"
    default_member_cap: Money = Decimal(0)
    parent_group_id: int | None = Field(default=None, gt=0)


class GroupAtDepartmentIn(GroupCreateIn):
    department_id: int = Field(gt=0)


class GroupParentIn(BaseModel):
    parent_group_id: int | None = Field(gt=0)


class OrganizationCreateIn(BaseModel):
    name: str = Field(min_length=1)
    departments_envelope: Money = Decimal(0)
    systems_envelope: Money = Decimal(0)
    budget_period: str = Field(default="1mo", pattern=r"^\d+(s|m|h|d|mo)$")


class RoleBindingIn(BaseModel):
    scope_kind: Literal["global", "organization", "department", "group"]
    scope_id: int | None = None
    role: str
    ad_group_dns: list[str]


class RateIn(BaseModel):
    model: str = Field(min_length=1)
    input_per_1m: Decimal = Field(ge=0)
    output_per_1m: Decimal = Field(ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)


class RateCardIn(BaseModel):
    rates: list[RateIn]
