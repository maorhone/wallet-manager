from pydantic import BaseModel, Field

from app.schemas.common import Money


class DepartmentBudgetIn(BaseModel):
    budget: Money
    budget_period: str = Field(pattern=r"^\d+(s|m|h|d|mo)$")  # LiteLLM duration string
