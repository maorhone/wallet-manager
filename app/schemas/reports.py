"""Report responses: Decimal internally, JSON numbers at the API boundary."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PlainSerializer

ReportNumber = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]
AllocationLevel = Literal["department", "group", "employee"]


class ModelAllocation(BaseModel):
    model: str
    allocation: ReportNumber | None
    spend: ReportNumber
    pct: ReportNumber | None


class AllocationValues(BaseModel):
    total: ReportNumber
    allocated: ReportNumber
    spend: ReportNumber
    remaining: ReportNumber
    pct: ReportNumber
    state: str


class AllocationRow(AllocationValues):
    id: int | str
    name: str
    kind: str
    base: ReportNumber | None = None
    grants: ReportNumber | None = None
    base_cap: ReportNumber | None = None
    shares_total: ReportNumber | None = None
    model_caps: dict[str, ReportNumber] | None = None
    models: list[ModelAllocation] = Field(default_factory=list)


class AllocationReport(AllocationValues):
    level: AllocationLevel
    id: int | str
    name: str
    period: str
    models: list[ModelAllocation]
    rows: list[AllocationRow]
    spend_taken_at: datetime | None


class GrantTotals(BaseModel):
    departments: int
    groups: int
    grants: int
    amount: ReportNumber
    shared: ReportNumber
    unshared: ReportNumber
    used: ReportNumber
    returned: ReportNumber


class ProjectGrants(GrantTotals):
    project_ref: str | None


class GrantsReport(BaseModel):
    rows: list[ProjectGrants]
    totals: GrantTotals
