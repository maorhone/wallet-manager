"""First-login onboarding and pending placement (SPEC §9.1, §9.2, §9.5)."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics
from app.config import Settings
from app.db import LockKind, employee_lock_id
from app.directory import Directory, DirectoryUnavailable
from app.errors import ApiError, directory_unavailable, member_state_conflict, scope_forbidden
from app.models import Department, Employee, Grp
from app.permissions import ADMIN, Principal, can_place, target_tier
from app.services.lifecycle import check_target, guard_idle, provision, unapplied
from app.writes import WriteContext

_NOTES = [
    "Sign in to Claude Code with your organization account; it sends your ADFS token.",
    "Your personal key is mapped to your account by the proxy; there is no key to copy.",
]


@dataclass(frozen=True)
class Placement:
    group_id: int | None
    department_id: int | None
    reason: str | None  # None when exactly one budget group was found


def find_placement(
    directory: Directory, upn: str, groups: list[Grp], departments: list[Department]
) -> Placement:
    """§9.1. May raise ``DirectoryUnavailable``."""
    if directory.display_name(upn) is None:
        return Placement(None, None, "not_in_directory")
    dns = set(directory.groups_of(upn))
    mine = [g for g in groups if g.status == "active" and g.ad_group_dn in dns]
    if len(mine) == 1:
        return Placement(mine[0].id, mine[0].department_id, None)
    if not mine:
        depts = [d.id for d in departments if d.ad_group_dn in dns]
        return Placement(None, depts[0] if len(depts) == 1 else None, "no_budget_group")
    common = {g.department_id for g in mine}
    return Placement(None, common.pop() if len(common) == 1 else None, "multiple_budget_groups")


async def _named(db: AsyncSession, model, id: int | None) -> dict | None:
    row = await db.get(model, id) if id is not None else None
    return {"id": row.id, "name": row.name} if row else None


async def onboarding_status(db: AsyncSession, settings: Settings, upn: str, request_id: str):
    """The §9.2 response, derived from the employee and their outbox rows. Never writes."""
    emp = await db.get(Employee, upn)
    if emp is None:
        raise ApiError(404, "NOT_FOUND", "Unknown employee")
    reason = None
    if emp.status == "pending_placement":
        status, reason = "pending_placement", emp.pending_reason
    elif emp.status in ("blocked", "left"):
        status, reason = "blocked", "kicked" if emp.status == "blocked" else "deleted"
    else:
        pending, failed = await unapplied(db, upn)
        status = "provisioning" if pending or failed else "ready"
        reason = "provisioning_failed" if failed else None
    group = await _named(db, Grp, emp.group_id)
    department_id = emp.pending_department_id
    if emp.group_id is not None:
        department_id = (await db.get(Grp, emp.group_id)).department_id
    out = {
        "upn": upn, "status": status, "reason": reason, "group": group,
        "department": await _named(db, Department, department_id), "request_id": request_id,
    }  # fmt: skip
    if status == "ready":  # a placeholder token, never a secret (NFR5, D15)
        url = settings.litellm_public_url
        out["install"] = {
            "litellm_base_url": url, "auth": "adfs_jwt", "notes": _NOTES,
            "env": {
                "ANTHROPIC_BASE_URL": url,
                "ANTHROPIC_AUTH_TOKEN": "<your ADFS JWT, printed by the portal's login helper>",
            },
        }  # fmt: skip
    return out


async def onboard(
    ctx: WriteContext, directory: Directory, upn: str, client_id: str
) -> tuple[dict, bool]:
    """Create the employee on the first call; later calls change nothing (§9.2)."""
    await ctx.lock(LockKind.EMPLOYEE, employee_lock_id(upn))
    if await ctx.db.get(Employee, upn) is None:
        groups = list((await ctx.db.scalars(select(Grp))).all())
        departments = list((await ctx.db.scalars(select(Department))).all())
        try:
            name = directory.display_name(upn)
            found = find_placement(directory, upn, groups, departments)
        except DirectoryUnavailable as exc:
            raise directory_unavailable() from exc
        emp = Employee(upn=upn, litellm_user_id=upn, display_name=name)
        if found.group_id is not None:
            emp.group_id = found.group_id
            emp.base_cap = next(g.default_member_cap for g in groups if g.id == found.group_id)
        else:
            emp.status, emp.pending_reason = "pending_placement", found.reason
            emp.pending_department_id, emp.pending_since = found.department_id, datetime.now(UTC)
        ctx.db.add(emp)
        await ctx.db.flush()
        if found.group_id is not None:
            await provision(ctx, emp)
        ctx.audit(
            "employee", upn, None,
            {"status": emp.status, "group_id": emp.group_id, "reason": found.reason},
            "onboarding_portal", client_id=client_id,
        )  # fmt: skip
        created = True
    else:
        created = False
    body = await onboarding_status(ctx.db, ctx.settings, upn, ctx.request_id)
    metrics.ONBOARDING_CALLS.labels(body["status"]).inc()
    return body, created


async def place(
    ctx: WriteContext, principal: Principal, directory: Directory, upn: str, group_id: int
) -> list[dict]:
    """Resolve a pending employee into a group and provision them as in onboarding (§9.5)."""
    emp = await ctx.db.get(Employee, upn)
    if emp is None:
        raise ApiError(404, "NOT_FOUND", "Unknown employee")
    role = await can_place(ctx.db, principal, emp.pending_department_id)
    check_target(role, principal.upn, upn, await target_tier(ctx.db, directory, upn), "place")
    await ctx.lock(LockKind.EMPLOYEE, employee_lock_id(upn))
    await ctx.db.refresh(emp)
    if emp.status not in (
        ("pending_placement", "left") if role == ADMIN else ("pending_placement",)
    ):
        raise member_state_conflict(emp.status)
    await guard_idle(ctx.db, upn)
    group = await ctx.db.get(Grp, group_id)
    if group is None:
        raise ApiError(404, "NOT_FOUND", "Group not found")
    if role != ADMIN and group.department_id != emp.pending_department_id:
        raise scope_forbidden("The group is outside the employee's department")
    if group.status != "active":
        raise member_state_conflict(emp.status)

    before = {"status": emp.status, "pending_reason": emp.pending_reason}
    emp.group_id, emp.base_cap, emp.status = group.id, group.default_member_cap, "active"
    emp.left_at = emp.pending_department_id = emp.pending_reason = emp.pending_since = None
    await provision(ctx, emp)
    after = {"status": "active", "group_id": group.id, "base_cap": group.default_member_cap}
    ctx.audit(
        "employee", upn, before, after, role, action="place", group_id=group.id,
        department_id=group.department_id,
    )  # fmt: skip
    metrics.LIFECYCLE_ACTIONS.labels("place").inc()
    return [{"entity": "employee", "id": upn, "values": after}]


async def pending_list(db: AsyncSession, department: int | None | str = "all") -> list[dict]:
    """Pending employees: one department, ``None`` = unknown department, ``"all"`` = everyone."""
    q = select(Employee).where(Employee.status == "pending_placement")
    if department is None:
        q = q.where(Employee.pending_department_id.is_(None))
    elif department != "all":
        q = q.where(Employee.pending_department_id == department)
    names = {d.id: d.name for d in await db.scalars(select(Department))}
    rows = await db.scalars(q.order_by(Employee.pending_since, Employee.upn))
    return [
        {
            "upn": e.upn, "display_name": e.display_name, "reason": e.pending_reason,
            "pending_since": e.pending_since.isoformat() if e.pending_since else None,
            "department_id": e.pending_department_id,
            "department_name": names.get(e.pending_department_id),
        }
        for e in rows
    ]  # fmt: skip
