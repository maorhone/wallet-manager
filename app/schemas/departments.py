from pydantic import BaseModel, Field

from app.schemas.common import Money


class DepartmentBudgetIn(BaseModel):
    budget: Money
    budget_period: str = Field(pattern=r"^\d+(s|m|h|d|mo)$")  # LiteLLM duration string


class SplitRow(BaseModel):
    group_id: int
    base: Money


class DepartmentSplitIn(BaseModel):
    groups: list[SplitRow]
    pmo_pool: Money
    reserve: Money


ModelAmounts = dict[str, Money]  # model -> credits; absent or 0 means none
