from pydantic import BaseModel


class NamedRef(BaseModel):
    id: int
    name: str


class RoleOut(BaseModel):
    role: str
    scope_kind: str
    scope_id: int | None = None
    scope_name: str | None = None


class ScopeOut(BaseModel):
    kind: str  # self | group | department | platform
    id: int | None = None
    name: str | None = None
    role: str | None = None


class MeOut(BaseModel):
    upn: str
    display_name: str
    group: NamedRef | None
    department: NamedRef | None
    roles: list[RoleOut]
    scopes: list[ScopeOut]
    timezone: str
    current_period: str
    dev_auth: bool
