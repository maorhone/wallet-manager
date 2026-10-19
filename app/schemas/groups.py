from pydantic import BaseModel, NonNegativeInt

from app.schemas.common import Money


class GroupLimitsIn(BaseModel):
    tpm_limit: NonNegativeInt | None = None
    model_tpm_limits: dict[str, NonNegativeInt] = {}


class MemberCapIn(BaseModel):
    upn: str
    base_cap: Money


class MemberLimitsIn(BaseModel):
    max_parallel: NonNegativeInt | None = None
    rpm_limit: NonNegativeInt | None = None
    model_tpm_limits: dict[str, NonNegativeInt] = {}


class MemberDefaultsIn(BaseModel):
    cap: Money
    model_caps: dict[str, Money] = {}
