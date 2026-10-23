from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import Money


class ServiceKeyIn(BaseModel):
    owner_kind: Literal["group", "system", "initiative"]
    owner_id: int = Field(gt=0)
    alias: str = Field(min_length=1, max_length=200)
    max_budget: Money
    max_parallel: int = Field(ge=0)  # 0 is rejected by the policy minimum (LIMIT_OUT_OF_POLICY)
    rpm_limit: int = Field(ge=0)
    models: list[str] = Field(default_factory=list, max_length=100)
