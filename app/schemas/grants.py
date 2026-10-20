from typing import Annotated

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from app.schemas.common import Money

PositiveMoney = Annotated[Money, Field(gt=0)]


class GrantIn(BaseModel):
    group_id: int
    amount: PositiveMoney
    model: str | None = Field(default=None, min_length=1)
    starts_at: AwareDatetime
    expires_at: AwareDatetime
    project_ref: str = Field(min_length=1)
    purpose: str = Field(min_length=1)


class GrantPatchIn(BaseModel):
    amount: PositiveMoney | None = None
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _something(self):
        if self.amount is None and self.expires_at is None:
            raise ValueError("amount or expires_at is required")
        return self


class GrantCloseIn(BaseModel):
    reason: str | None = None


class ShareIn(BaseModel):
    upn: str = Field(min_length=1)
    amount: PositiveMoney
    expires_at: AwareDatetime


class SharePatchIn(BaseModel):
    amount: PositiveMoney | None = None
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _something(self):
        if self.amount is None and self.expires_at is None:
            raise ValueError("amount or expires_at is required")
        return self
