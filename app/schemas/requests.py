from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import Money


class TargetIn(BaseModel):
    """D-046..D-048: an existing initiative or internal system the requester owns."""

    kind: Literal["initiative", "system"]
    id: int = Field(gt=0)


class RequestIn(BaseModel):
    type: Literal["credits", "initiative", "throughput"]
    amount: Money | None = None
    payload: dict = Field(default_factory=dict)
    target: TargetIn | None = None


class DecideIn(BaseModel):
    decision: Literal["approve", "reject", "forward", "apply"]
    amount: Money | None = None
    comment: str | None = Field(default=None, max_length=1000)
    # D-045: the AI team supplies the technical values when it applies a narrative request
    max_parallel: int | None = None
    rpm_limit: int | None = None
    model_tpm_limits: dict | None = None
