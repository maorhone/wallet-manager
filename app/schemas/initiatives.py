from datetime import date

from pydantic import BaseModel, Field

from app.schemas.common import Money


class InitiativeIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    owner_upn: str = Field(min_length=1, max_length=320)
    period_start: date | None = None
    period_end: date | None = None
    budget: Money
