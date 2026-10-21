from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import Money

# §11.1 PUT /policies: a partial map of policy keys, raw (not a strict schema) so an unknown
# or ``member_defaults.*`` key can be reported with ``details.key`` instead of a generic
# FastAPI validation error.
PoliciesIn = dict[str, Any]


class DepartmentCreateIn(BaseModel):
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


class RoleBindingIn(BaseModel):
    scope_kind: Literal["global", "department", "group"]
    scope_id: int | None = None
    role: str
    ad_group_dns: list[str]
