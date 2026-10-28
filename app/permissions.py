from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import LockKind, lock_parent
from app.directory import Directory
from app.errors import ApiError
from app.models import Department, Grp, Initiative, RoleBinding

ADMIN, REP, PMO, HEAD, VIEWER = "platform_admin", "department_rep", "pmo", "group_head", "viewer"
ORG_ALLOCATOR = "org_allocator"
AI_TEAM = "ai_team"  # D-065: the only role (besides admin) that changes rate limits


@dataclass(frozen=True)
class Binding:
    role: str
    scope_kind: str  # global | organization | department | group
    scope_id: int | None


@dataclass
class Principal:
    upn: str
    display_name: str
    bindings: list[Binding]
    inherited: list[Binding] = field(default_factory=list)

    def has(self, role: str, scope_kind: str, scope_id: int | None = None) -> bool:
        return Binding(role, scope_kind, scope_id) in self.bindings + self.inherited

    @property
    def is_admin(self) -> bool:
        return self.has(ADMIN, "global")

    def department_roles(self, department_id: int) -> set[str]:
        roles = {r for r in (REP, PMO, VIEWER) if self.has(r, "department", department_id)}
        if self.has(VIEWER, "global"):
            roles.add(VIEWER)
        if self.is_admin:
            roles.add(ADMIN)
        if self.has(AI_TEAM, "global"):
            roles.add(AI_TEAM)
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


async def inherit_management(session: AsyncSession, p: Principal) -> None:
    """Derive effective management scopes; never persist inherited role bindings.

    The top-node manager delegates as a representative and group head below its org.
    Direct bindings stay separate for /me and role/tier reporting. Recompute on each
    request and after acquiring a hierarchy write lock so moves immediately revoke access.
    """
    p.inherited = []
    roots = [b.scope_id for b in p.bindings if b.role == HEAD and b.scope_kind == "group"]
    orgs = [
        b.scope_id for b in p.bindings if b.role == ORG_ALLOCATOR and b.scope_kind == "organization"
    ]
    if orgs:
        departments = list(
            await session.scalars(select(Department.id).where(Department.organization_id.in_(orgs)))
        )
        p.inherited.extend(Binding(REP, "department", did) for did in departments)
        p.inherited.extend(
            Binding(HEAD, "group", gid)
            for gid in await session.scalars(
                select(Grp.id).where(Grp.department_id.in_(departments))
            )
        )
    if roots:
        tree = select(Grp.id, Grp.department_id).where(Grp.id.in_(roots)).cte(recursive=True)
        tree = tree.union_all(
            select(Grp.id, Grp.department_id).join(
                tree,
                (Grp.parent_group_id == tree.c.id) & (Grp.department_id == tree.c.department_id),
            )
        )
        p.inherited.extend(
            Binding(HEAD, "group", gid) for gid in await session.scalars(select(tree.c.id))
        )


async def hierarchy_role(
    session, p: Principal, department_id: int, *, group_id: int | None = None
) -> str:
    """Management above a group's root: admin, org allocator or department rep."""
    dept = await session.get(Department, department_id)
    if dept is None:
        raise _not_found("Department")
    if p.is_admin:
        return ADMIN
    if p.has(ORG_ALLOCATOR, "organization", dept.organization_id):
        return ORG_ALLOCATOR
    roles = p.department_roles(department_id)
    if group_id is not None and (
        p.has(HEAD, "group", group_id) or p.has(VIEWER, "group", group_id)
    ):
        roles.add(VIEWER)
    return _pick(roles, (REP,), _DEPT_VIEW)


async def can_create_group(session, p: Principal, department_id: int, parent_id: int | None) -> str:
    if await session.get(Department, department_id) is None:
        raise _not_found("Department")
    if p.department_roles(department_id) & {ADMIN, REP}:
        return await hierarchy_role(session, p, department_id)
    if parent_id is not None:
        parent = await session.get(Grp, parent_id)
        # Check department before acquiring the parent's lock: never lock another
        # department on behalf of a client-supplied parent scope.
        if parent is not None and parent.department_id == department_id:
            return _pick(await _group_roles(session, p, parent_id), (HEAD,), _GROUP_VIEW)
    return await hierarchy_role(session, p, department_id)


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


_DEPT_VIEW = (ADMIN, AI_TEAM, REP, PMO, VIEWER)
_GROUP_VIEW = (ADMIN, AI_TEAM, REP, PMO, HEAD, VIEWER)


async def _dept_roles(session: AsyncSession, p: Principal, department_id: int) -> set[str]:
    if await session.get(Department, department_id) is None:
        raise _not_found("Department")
    return p.department_roles(department_id)


async def _group_roles(session: AsyncSession, p: Principal, group_id: int) -> set[str]:
    group = await session.get(Grp, group_id)
    if group is None:
        raise _not_found("Group")
    if any(b.role in (HEAD, ORG_ALLOCATOR) for b in p.bindings):
        # Serialize inherited authorization with reparenting for existing write paths too.
        await lock_parent(session, LockKind.DEPARTMENT, group.department_id)
        await inherit_management(session, p)
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


def can_manage_grants(p: Principal, department_id: int) -> str:
    """FR-P1: admin and the department's PMO; everyone else is forbidden."""
    roles = p.department_roles(department_id)
    for role in (ADMIN, PMO):
        if role in roles:
            return role
    raise _forbidden()


async def can_view_grants(
    session, p: Principal, department_id: int, group_id: int | None = None
) -> str:
    """Admin, PMO, rep or viewer on the department; head or viewer on the group."""
    roles = (
        await _group_roles(session, p, group_id)
        if group_id
        else await _dept_roles(session, p, department_id)
    )
    return _pick(roles, _GROUP_VIEW, _GROUP_VIEW)


async def group_roles_of(session, p: Principal, group_id: int) -> set[str]:
    return await _group_roles(session, p, group_id)


async def headed_groups(session, p: Principal, group_ids) -> set[int]:
    """The subset of ``group_ids`` the caller heads (D-056/D-057)."""
    return {
        g for g in set(group_ids) if g is not None and HEAD in await _group_roles(session, p, g)
    }


async def can_view_group(session, p: Principal, group_id: int) -> str:
    return _pick(await _group_roles(session, p, group_id), _GROUP_VIEW, _GROUP_VIEW)


async def can_view_members(session, p: Principal, group_id: int) -> str:
    roles = await _group_roles(session, p, group_id)
    return _pick(roles, (ADMIN, REP, HEAD), _GROUP_VIEW)  # D11: not pmo, not viewer


async def can_edit_group_pots_limits(session, p: Principal, group_id: int) -> str:
    return _pick(await _group_roles(session, p, group_id), (ADMIN, REP), _GROUP_VIEW)


async def can_set_rate_limits(session, p: Principal, group_id: int) -> str:
    """D-065: rate limits are written by the AI team (or an admin) only."""
    return _pick(await _group_roles(session, p, group_id), (ADMIN, AI_TEAM), _GROUP_VIEW)


async def can_share(session, p: Principal, group_id: int) -> str:
    """FR-P3: admin, the department's rep, or the group's head create and change shares."""
    return _pick(await _group_roles(session, p, group_id), (ADMIN, REP, HEAD), _GROUP_VIEW)


async def can_edit_member_defaults(session, p: Principal, group_id: int) -> str:
    return _pick(await _group_roles(session, p, group_id), (ADMIN, HEAD), _GROUP_VIEW)


async def can_write_members(session, p: Principal, group_id: int) -> str:
    return _pick(await _group_roles(session, p, group_id), (ADMIN, REP, HEAD), _GROUP_VIEW)


# §9.4: roles allowed to run each member lifecycle action on a group's members
KICK_ROLES, MOVE_ROLES, DELETE_ROLES = (ADMIN, REP, HEAD), (ADMIN, REP), (ADMIN,)


async def can_kick(session, p: Principal, group_id: int) -> str:
    return _pick(await _group_roles(session, p, group_id), KICK_ROLES, _GROUP_VIEW)


async def can_move(session, p: Principal, group_id: int) -> str:
    return _pick(await _group_roles(session, p, group_id), MOVE_ROLES, _GROUP_VIEW)


async def can_delete_member(session, p: Principal, group_id: int) -> str:
    return _pick(await _group_roles(session, p, group_id), DELETE_ROLES, _GROUP_VIEW)


async def can_place(session, p: Principal, department_id: int | None) -> str:
    """§9.4: admin always (also unknown department); a rep only when the department is known."""
    if p.is_admin:
        return ADMIN
    if department_id is None:
        raise _not_found("Employee")
    return _pick(await _dept_roles(session, p, department_id), (ADMIN, REP), _DEPT_VIEW)


async def can_view_pending(session, p: Principal, department_id: int | None) -> str:
    """§9.5: the platform list (``None``) is admin only; a department list is rep or admin."""
    if department_id is None:
        if p.is_admin:
            return ADMIN
        raise _forbidden()
    return await can_place(session, p, department_id)


async def can_view_production(session, p: Principal, department_id: int) -> str:
    """§12.2 ייצור row: admin edits, the department's rep views; everyone else is forbidden."""
    roles = await _dept_roles(session, p, department_id)
    for role in (ADMIN, REP):
        if role in roles:
            return role
    raise _forbidden()


def can_manage_platform(p: Principal) -> str:
    """§12.5.5: policies, department/group creation and role bindings are admin only."""
    if not p.is_admin:
        raise _forbidden()
    return ADMIN


# §11.1 PUT /role-bindings: roles allowed per scope kind
ROLE_BINDING_SCOPES: dict[str, tuple[str, ...]] = {
    "organization": (ORG_ALLOCATOR,),
    "department": (REP, PMO, VIEWER),
    "group": (HEAD, VIEWER),
    "global": (ADMIN, VIEWER, AI_TEAM),
}


def tier_from_bindings(bindings: list[Binding]) -> str:
    """Highest role among ``bindings`` (DR9)."""
    roles = {b.role for b in bindings}
    for role, tier in (
        (ADMIN, "platform_admin"),
        (ORG_ALLOCATOR, "department_rep"),
        (REP, "department_rep"),
        (HEAD, "group_head"),
    ):
        if role in roles:
            return tier
    return "member"


async def target_tier(session: AsyncSession, directory: Directory, upn: str) -> str:
    """Highest role the target holds, from directory groups ∩ role_binding (DR9)."""
    return tier_from_bindings(await load_bindings(session, directory.groups_of(upn)))


async def can_view_service_keys(session, p: Principal, owner_kind: str, owner_id: int) -> str:
    if owner_kind == "initiative":
        initiative = await session.get(Initiative, owner_id)
        if initiative is None:
            raise _not_found("Initiative")
        if initiative.owner_upn == p.upn:  # the owner reads, never writes (5.6)
            return "owner"
        return await can_view_service_keys(session, p, "group", initiative.group_id)
    if owner_kind == "group":
        roles = await _group_roles(session, p, owner_id)
        return _pick(roles, (ADMIN, REP, HEAD, VIEWER), _GROUP_VIEW)
    from app.models import ProductionSystem

    system = await session.get(ProductionSystem, owner_id)
    if system is None:
        raise _not_found("System")
    if system.funding == "organization":
        if p.is_admin:
            return ADMIN
        if p.has(ORG_ALLOCATOR, "organization", system.organization_id):
            return ORG_ALLOCATOR
        if system.owner_upn == p.upn:  # the owner views, never writes (D-077)
            return "owner"
        raise _forbidden()
    try:
        return await can_view_production(session, p, system.department_id)
    except ApiError:
        if system.owner_upn == p.upn:  # the owner reads limits and keys, never writes (D-048)
            return "owner"
        # review-p5-r17 B2: the head of the section the system is delegated to reads it too
        if system.managing_group_id is not None:
            if HEAD in await _group_roles(session, p, system.managing_group_id):
                return HEAD
        raise


async def can_manage_service_keys(session, p: Principal, owner_kind: str, owner_id: int) -> str:
    viewed = await can_view_service_keys(session, p, owner_kind, owner_id)
    if owner_kind == "initiative":  # managed by the section's head or an admin
        owner_kind, owner_id = "group", (await session.get(Initiative, owner_id)).group_id
    if owner_kind == "group":
        # Always re-resolve under the department lock: an owner's read access skips it (blocker 8).
        roles = await _group_roles(session, p, owner_id)
        if p.is_admin:
            return ADMIN
        if HEAD in roles:
            return HEAD
        raise _forbidden()
    if p.is_admin:
        return ADMIN
    if owner_kind == "system" and viewed == ORG_ALLOCATOR:
        return ORG_ALLOCATOR
    raise _forbidden()


def can_manage_org_systems(p: Principal, organization_id: int) -> str:
    """Org Systems (5.4): the platform admin and the organization's allocator write."""
    if p.is_admin:
        return ADMIN
    if p.has(ORG_ALLOCATOR, "organization", organization_id):
        return ORG_ALLOCATOR
    raise _forbidden()
