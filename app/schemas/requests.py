from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import Money


class RequestIn(BaseModel):
    type: Literal["credits", "initiative", "throughput"]
    amount: Money | None = None
    payload: dict = Field(default_factory=dict)


class DecideIn(BaseModel):
    decision: Literal["approve", "reject", "forward", "apply"]
    amount: Money | None = None
    comment: str | None = Field(default=None, max_length=1000)
