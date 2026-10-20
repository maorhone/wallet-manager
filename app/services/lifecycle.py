"""Member lifecycle actions (SPEC §9.3, §9.4): kick, restore, move, delete.

Each action takes the per-employee advisory lock, checks the employee's state, writes audit and the
chained outbox rows in §9.3 order, and returns the write-response entities.
"""

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select

from app import metrics
from app.db import LockKind, employee_lock_id
from app.directory import Directory
from app.errors import (
    ApiError,
    lifecycle_in_progress,
    member_state_conflict,
    scope_forbidden,
    self_allocation_forbidden,
)
from app.litellm_client import JwtKeyMappingDelete, KeyDelete, TeamMemberDelete, UserDelete
from app.models import (
    Department,
    Employee,
    EmployeeModelCap,
    Grant,
    GrantShare,
    Grp,
    LitellmWrite,
)
from app.permissions import (
    DELETE_ROLES,
    HEAD,
    KICK_ROLES,
    MOVE_ROLES,
    REP,
    Principal,
    can_delete_member,
    can_kick,
    can_move,
    target_tier,
)
from app.services.personal_values import OWNER_ROLE
from app.writes import WriteContext

JOINER = ("/user/new", "/team/member_add", "/key/generate", "/jwt/key/mapping/new")  # §9.3

# action -> (roles allowed, employee states it applies to)
_RULES = {
    "kick": (KICK_ROLES, ("active",)),
    "restore": (KICK_ROLES, ("blocked",)),
    "move": (MOVE_ROLES, ("active", "blocked")),
    "delete": (DELETE_ROLES, ("active", "blocked")),
}
_CAN = {"kick": can_kick, "restore": can_kick, "move": can_move, "delete": can_delete_member}


async def provision(ctx: WriteContext, e: Employee) -> None:
    """Queue the Joiner sequence for an employee in group order (§9.3 Onboarding / place)."""
    for endpoint in JOINER:
        await ctx.enqueue("employee", e.upn, endpoint, chain=True)


async def close_member_shares(
    ctx: WriteContext, upn: str, reason: Literal["move", "manual"], *,
    clock, client, reader, role: str = "system",
) -> None:  # fmt: skip
    """Close the employee's active grant shares (FR-P6), spend taken in the old team.

    Called before the membership changes. The rows are not chained to the move chain; the old
    team's ``/team/update`` is rebuilt when applied and no ``/team/member_update`` is queued
    (the membership is about to go).
    """
    from app.services.shares import close_shares  # shares imports this module

    rows = (
        await ctx.db.execute(
            select(GrantShare, Grant.group_id).join(Grant, Grant.id == GrantShare.grant_id)
            .where(GrantShare.upn == upn, GrantShare.status == "active")
            .order_by(GrantShare.id)
        )
    ).all()  # fmt: skip
    for group_id in sorted({g for _, g in rows}):
        await ctx.lock(LockKind.GROUP, group_id)
    if rows:
        await close_shares(
            ctx, [s for s, _ in rows], clock.now(), reason, client, reader, role,
            member_update=False,
        )  # fmt: skip


def check_target(role: str, actor_upn: str, upn: str, tier: str, action: str = "") -> None:
    """§9.4 target tiers: not yourself; heads act on plain members; reps not on reps or admins."""
    if upn == actor_upn:
        err = self_allocation_forbidden()
        err.details = {"action": action, "owner_role": OWNER_ROLE[tier]}
        raise err
    if (role == HEAD and tier != "member") or (
        role == REP and tier in ("department_rep", "platform_admin")
    ):
        raise scope_forbidden("You may not act on this person")


async def unapplied(db, upn: str) -> tuple[int, int]:
    """(pending, failed) outbox rows of the employee."""
    rows = dict(
        (
            await db.execute(
                select(LitellmWrite.status, func.count())
                .where(LitellmWrite.entity == "employee", LitellmWrite.entity_id == upn)
                .group_by(LitellmWrite.status)
            )
        ).all()
    )
    return rows.get("pending", 0), rows.get("failed", 0)


async def guard_idle(db, upn: str) -> None:
    """D23: move, delete and place wait until the employee's earlier outbox rows are applied."""
    pending, failed = await unapplied(db, upn)
    if pending or failed:
        raise lifecycle_in_progress(pending, failed)


def row_actions(principal: Principal, group: Grp, emp: Employee, tier: str) -> dict[str, bool]:
    """Which buttons the UI may show for a member row; same rules as the endpoints."""
    roles = principal.group_roles(group.id, group.department_id)
    out = {}
    for action, (allowed, states) in _RULES.items():
        role = next((r for r in allowed if r in roles), None)
        ok = role is not None and emp.status in states
        if ok:
            try:
                check_target(role, principal.upn, emp.upn, tier)
            except ApiError:
                ok = False
        out[action] = ok
    return out


async def _begin(ctx, principal, directory: Directory, group_id: int, upn: str, action: str):
    """Authorize, lock the employee, re-read them and check the action fits their state."""
    role = await _CAN[action](ctx.db, principal, group_id)
    group = await ctx.db.get(Grp, group_id)
    emp = await ctx.db.get(Employee, upn)
    check_target(role, principal.upn, upn, await target_tier(ctx.db, directory, upn), action)
    if emp is None or emp.group_id != group_id:
        raise scope_forbidden("Target is not a member of the group")
    await ctx.lock(LockKind.EMPLOYEE, employee_lock_id(upn))
    await ctx.db.refresh(emp)
    return role, group, emp


def _may_target(principal: Principal, group: Grp) -> bool:
    """Admins move anywhere; a representative only within a department they represent."""
    return principal.is_admin or REP in principal.department_roles(group.department_id)


def _check_state(emp: Employee, action: str) -> None:
    if emp.status not in _RULES[action][1]:
        raise member_state_conflict(emp.status)


def _done(ctx, role, group, emp, action, before, after) -> list[dict]:
    ctx.audit(
        "employee", emp.upn, before, after, role, action=action, group_id=group.id,
        department_id=group.department_id,
    )  # fmt: skip
    metrics.LIFECYCLE_ACTIONS.labels(action).inc()
    return [{"entity": "employee", "id": emp.upn, "values": after}]


async def _set_status(ctx, principal, directory, group_id, upn, action, status, endpoints):
    role, group, emp = await _begin(ctx, principal, directory, group_id, upn, action)
    _check_state(emp, action)
    before, emp.status = {"status": emp.status}, status
    for endpoint in endpoints:
        await ctx.enqueue("employee", upn, endpoint, chain=True)
    return _done(ctx, role, group, emp, action, before, {"status": status})


async def kick(ctx, principal, directory, group_id, upn) -> list[dict]:
    steps = ("/key/block", "/jwt/key/mapping/update")
    return await _set_status(ctx, principal, directory, group_id, upn, "kick", "blocked", steps)


async def restore(ctx, principal, directory, group_id, upn) -> list[dict]:
    steps = ("/key/unblock", "/jwt/key/mapping/update")
    return await _set_status(ctx, principal, directory, group_id, upn, "restore", "active", steps)


async def move(
    ctx, principal, directory, group_id, upn, to_group_id: int, *, clock, client, reader
) -> list[dict]:
    """Add to the new team, repoint the key, remove from the old team (the key keeps a team)."""
    role, old, emp = await _begin(ctx, principal, directory, group_id, upn, "move")
    _check_state(emp, "move")
    await guard_idle(ctx.db, upn)
    new = await ctx.db.get(Grp, to_group_id)
    if new is None:
        raise ApiError(404, "NOT_FOUND", "Group not found")
    if not _may_target(principal, new):
        raise scope_forbidden("The target group is outside your department")
    if new.id == old.id or new.status != "active":
        raise member_state_conflict(emp.status)

    await close_member_shares(
        ctx, upn, "move", role=role, clock=clock, client=client, reader=reader
    )
    before = {"group_id": old.id, "base_cap": emp.base_cap}
    emp.group_id, emp.base_cap = new.id, new.default_member_cap
    await ctx.db.execute(sql_delete(EmployeeModelCap).where(EmployeeModelCap.upn == upn))
    await ctx.enqueue("employee", upn, "/team/member_add", chain=True)
    await ctx.enqueue("employee", upn, "/key/update", chain=True)
    frozen = TeamMemberDelete(team_id=old.litellm_team_id, user_id=upn).payload()
    await ctx.enqueue("employee", upn, "/team/member_delete", payload=frozen, chain=True)
    after = {"group_id": new.id, "base_cap": new.default_member_cap, "model_caps": {}}
    return _done(ctx, role, new, emp, "move", before, {**after, "from_group_id": old.id})


async def delete_member(
    ctx, principal, directory, group_id, upn, confirm_upn: str, *, clock, client, reader
) -> list[dict]:
    """Remove the key before the membership (D16); all targets are fixed at enqueue time."""
    role, group, emp = await _begin(ctx, principal, directory, group_id, upn, "delete")
    if confirm_upn != upn:
        raise ApiError(422, "CONFIRMATION_MISMATCH", "confirm_upn does not match the employee")
    _check_state(emp, "delete")
    await guard_idle(ctx.db, upn)

    await close_member_shares(
        ctx, upn, "manual", role=role, clock=clock, client=client, reader=reader
    )
    before = {"status": emp.status, "base_cap": emp.base_cap}
    mapping = JwtKeyMappingDelete(
        jwt_issuer=ctx.settings.jwt_issuer, jwt_claim_name="upn", jwt_claim_value=upn
    )
    steps = [("/jwt/key/mapping/delete", mapping)]
    if emp.key_token_hash:
        steps.append(("/key/delete", KeyDelete(keys=[emp.key_token_hash])))
    steps += [
        ("/team/member_delete", TeamMemberDelete(team_id=group.litellm_team_id, user_id=upn)),
        ("/user/delete", UserDelete(user_ids=[upn])),
    ]
    for endpoint, body in steps:
        await ctx.enqueue("employee", upn, endpoint, payload=body.payload(), chain=True)
    emp.status, emp.left_at, emp.key_token_hash = "left", datetime.now(UTC), None
    await ctx.db.execute(sql_delete(EmployeeModelCap).where(EmployeeModelCap.upn == upn))
    return _done(ctx, role, group, emp, "delete", before, {"status": "left"})


async def move_targets(db, principal: Principal, group_id: int) -> list[dict]:
    """Active groups the actor may move members of ``group_id`` to."""
    await can_move(db, principal, group_id)
    q = select(Grp).where(Grp.status == "active", Grp.id != group_id).order_by(Grp.id)
    out = [g for g in await db.scalars(q) if _may_target(principal, g)]
    names = {d.id: d.name for d in await db.scalars(select(Department))}
    return [
        {
            "group_id": g.id,
            "name": g.name,
            "department_id": g.department_id,
            "department_name": names[g.department_id],
        }  # fmt: skip
        for g in out
    ]
