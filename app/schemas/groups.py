from pydantic import BaseModel, NonNegativeInt


class GroupLimitsIn(BaseModel):
    tpm_limit: NonNegativeInt | None = None
    model_tpm_limits: dict[str, NonNegativeInt] = {}
