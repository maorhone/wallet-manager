from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.directory import Directory
from app.errors import ApiError
from app.models import Department, Grp, RoleBinding

ADMIN, REP, PMO, HEAD, VIEWER = "platform_admin", "department_rep", "pmo", "group_head", "viewer"


@dataclass(frozen=True)
class Binding:
    role: str
    scope_kind: str  # global | department | group
    scope_id: int | None


@dataclass
class Principal:
    upn: str
    display_name: str
    bindings: list[Binding]

    def has(self, role: str, scope_kind: str, scope_id: int | None = None) -> bool:
        return Binding(role, scope_kind, scope_id) in self.bindings

    @property
    def is_admin(self) -> bool:
        return self.has(ADMIN, "global")

    def department_roles(self, department_id: int) -> set[str]:
        roles = {r for r in (REP, PMO, VIEWER) if self.has(r, "department", department_id)}
        if self.has(VIEWER, "global"):
            roles.add(VIEWER)
        if self.is_admin:
            roles.add(ADMIN)
        return roles

    def group_roles(self, group_id: int, department_id: int) -> set[str]:
        roles = self.department_roles(department_id)
        if self.has(HEAD, "group", group_id):
            roles.add(HEAD)
        if self.has(VIEWER, "group", group_id):
            roles.add(VIEWER)
        return roles


async def load_bindings(session: AsyncSession, groups: list[str]) -> list[Binding]:
    """role_binding ∩ AD groups; recomputed on every request."""
    if not groups:
        return []
    rows = (
        await session.execute(select(RoleBinding).where(RoleBinding.ad_group_dn.in_(groups)))
    ).scalars()
    return list(dict.fromkeys(Binding(r.role, r.scope_kind, r.scope_id) for r in rows))


def _forbidden() -> ApiError:
    return ApiError(403, "FORBIDDEN", "Your role does not permit this action")


def _not_found(what: str) -> ApiError:
    return ApiError(404, "NOT_FOUND", f"{what} not found")


def _pick(roles: set[str], allowed: tuple[str, ...], visible: tuple[str, ...]) -> str:
    """Return the authorizing role; 404 if the entity is not visible at all, else 403."""
    if not roles & set(visible):
        raise _not_found("Entity")
    for role in allowed:
        if role in roles:
            return role
    raise _forbidden()


_DEPT_VIEW = (ADMIN, REP, PMO, VIEWER)
_GROUP_VIEW = (ADMIN, REP, PMO, HEAD, VIEWER)


async def _dept_roles(session: AsyncSession, p: Principal, department_id: int) -> set[str]:
    if await session.get(Department, department_id) is None:
        raise _not_found("Department")
    return p.department_roles(department_id)


async def _group_roles(session: AsyncSession, p: Principal, group_id: int) -> set[str]:
    group = await session.get(Grp, group_id)
    if group is None:
        raise _not_found("Group")
    return p.group_roles(group_id, group.department_id)


async def can_view_department(session, p: Principal, department_id: int) -> str:
    return _pick(await _dept_roles(session, p, department_id), _DEPT_VIEW, _DEPT_VIEW)


async def can_edit_split(session, p: Principal, department_id: int) -> str:
    roles = await _dept_roles(session, p, department_id)
    return _pick(roles, (ADMIN, REP), _DEPT_VIEW)


def can_edit_dept_budget(p: Principal) -> str:
    if not p.is_admin:
        raise _forbidden()
    return ADMIN


async def can_view_group(session, p: Principal, group_id: int) -> str:
    return _pick(await _group_roles(session, p, group_id), _GROUP_VIEW, _GROUP_VIEW)


async def can_view_members(session, p: Principal, group_id: int) -> str:
    roles = await _group_roles(session, p, group_id)
    return _pick(roles, (ADMIN, REP, HEAD), _GROUP_VIEW)  # D11: not pmo, not viewer


async def can_edit_group_pots_limits(session, p: Principal, group_id: int) -> str:
    return _pick(await _group_roles(session, p, group_id), (ADMIN, REP), _GROUP_VIEW)


async def can_edit_member_defaults(session, p: Principal, group_id: int) -> str:
    return _pick(await _group_roles(session, p, group_id), (ADMIN, HEAD), _GROUP_VIEW)


async def can_write_members(session, p: Principal, group_id: int) -> str:
    return _pick(await _group_roles(session, p, group_id), (ADMIN, REP, HEAD), _GROUP_VIEW)


async def target_tier(session: AsyncSession, directory: Directory, upn: str) -> str:
    """Highest role the target holds, from directory groups ∩ role_binding (DR9)."""
    roles = {b.role for b in await load_bindings(session, directory.groups_of(upn))}
    for role, tier in ((ADMIN, "platform_admin"), (REP, "department_rep"), (HEAD, "group_head")):
        if role in roles:
            return tier
    return "member"
