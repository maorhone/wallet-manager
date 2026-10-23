from pydantic import BaseModel, Field

from app.schemas.common import Money


class OrgSystemIn(BaseModel):
    name: str
    budget: Money
    owner_upn: str | None = None
    responsible_department_id: int | None = Field(default=None, gt=0)


class KeyMoveIn(BaseModel):
    target_system_id: int = Field(gt=0)
