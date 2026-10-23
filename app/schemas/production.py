from pydantic import BaseModel, Field

from app.schemas.common import Money


class ProductionOrgIn(BaseModel):
    budget: Money
    budget_period: str | None = Field(default=None, pattern=r"^\d+(s|m|h|d|mo)$")


class ProductionSystemIn(BaseModel):
    name: str
    budget: Money
