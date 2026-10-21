from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import Money


class ServiceKeyIn(BaseModel):
    owner_kind: Literal["group", "system"]
    owner_id: int = Field(gt=0)
    alias: str = Field(min_length=1, max_length=200)
    max_budget: Money
    max_parallel: int
    rpm_limit: int
    models: list[str] = Field(default_factory=list)
