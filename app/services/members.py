import asyncio
from decimal import Decimal

from sqlalchemy import select

from app.db import LockKind
from app.directory import Directory
from app.errors import invalid_request, scope_forbidden
from app.models import Employee, EmployeeModelCap, GroupModelPot, Grp, Policy
from app.permissions import (
    Principal,
    can_edit_member_defaults,
    can_write_members,
    target_tier,
)
from app.schemas.groups import MemberCapIn, MemberDefaultsIn, MemberLimitsIn
from app.services.personal_values import authorize, authorize_rows
from app.services.validation import check_limit, check_model_sums, check_sum
from app.writes import WriteContext


async def _members(db, group_id: int) -> dict[str, Employee]:
    rows = await db.scalars(
        select(Employee).where(Employee.group_id == group_id, Employee.status != "left")
    )
    return {e.upn: e for e in rows}


async def _target(ctx: WriteContext, directory: Directory, group: Grp, upn: str):
    """The employee row and its DR9 tier; unknown or foreign upns are out of scope."""
    employee = await ctx.db.get(Employee, upn)
    if employee is None:
        raise scope_forbidden("Target is not an active member of the group")
    return employee, await target_tier(ctx.db, directory, upn)


async def set_caps(
    ctx: WriteContext,
    principal: Principal,
    directory: Directory,
    group_id: int,
    body: list[MemberCapIn],
) -> list[dict]:
    """FR-G1: bulk base caps in one transaction; Σ caps <= base budget × over-allocation factor."""
    await can_write_members(ctx.db, principal, group_id)
    group = await ctx.db.get(Grp, group_id)
    if len({r.upn for r in body}) != len(body):
        raise invalid_request("Duplicate upn in request")
    await ctx.lock(LockKind.GROUP, group_id)
    members = await _members(ctx.db, group_id)

    targets = []
    for row in body:
        employee = members.get(row.upn) or await ctx.db.get(Employee, row.upn)
        if employee is None:
            employee = Employee(upn=row.upn, group_id=None)  # unknown: SCOPE_FORBIDDEN below
        targets.append((employee, await target_tier(ctx.db, directory, row.upn)))
    roles = authorize_rows(principal, targets, group)

    new = {r.upn: r.base_cap for r in body}
    total = sum((new.get(u, e.base_cap) for u, e in members.items()), Decimal(0))
    changed = [r.upn for r in body if members[r.upn].base_cap != r.base_cap]
    rows = [{"upn": u, "code": "PARENT_OVERALLOCATED"} for u in changed]
    if err := check_sum(total, group.base_budget, group.overalloc_factor, rows):
        raise err
    await asyncio.sleep(0)  # yield after the sums are read; tests widen this to prove the lock

    entities = []
    for (employee, _), row, role in zip(targets, body, roles, strict=True):
        if row.upn not in changed:
            continue
        before, after = {"base_cap": employee.base_cap}, {"base_cap": row.base_cap}
        employee.base_cap = row.base_cap
        ctx.audit(
            "employee", row.upn, before, after, role,
            group_id=group_id, department_id=group.department_id,
        )  # fmt: skip
        await ctx.enqueue("employee", row.upn, "/team/member_update")
        entities.append({"entity": "employee", "id": row.upn, "values": after})
    return entities


async def set_model_caps(
    ctx: WriteContext,
    principal: Principal,
    directory: Directory,
    group_id: int,
    upn: str,
    body: dict[str, Decimal],
) -> list[dict]:
    """FR-G3: Σ personal caps for a model <= the group's pot × over-allocation factor."""
    await can_write_members(ctx.db, principal, group_id)
    group = await ctx.db.get(Grp, group_id)
    employee, tier = await _target(ctx, directory, group, upn)
    role = authorize(principal, employee, group, tier)
    await ctx.lock(LockKind.GROUP, group_id)

    new = {m: a for m, a in body.items() if a > 0}
    caps = list(
        await ctx.db.scalars(
            select(EmployeeModelCap)
            .join(Employee, Employee.upn == EmployeeModelCap.upn)
            .where(Employee.group_id == group_id, Employee.status != "left")
        )
    )
    others: dict[str, dict] = {}
    for c in caps:
        if c.upn != upn and c.model in new:
            others.setdefault(c.upn, {})[c.model] = c.amount
    pots = {
        p.model: p.amount
        for p in await ctx.db.scalars(
            select(GroupModelPot).where(GroupModelPot.group_id == group_id)
        )
    }
    if err := check_model_sums({**others, upn: new}, pots, group.overalloc_factor):
        raise err
    await asyncio.sleep(0)

    mine = {c.model: c for c in caps if c.upn == upn}
    for model, row in mine.items():
        if model in new:
            row.amount = new[model]
        else:
            await ctx.db.delete(row)
    for model, amount in new.items():
        if model not in mine:
            ctx.db.add(EmployeeModelCap(upn=upn, model=model, amount=amount))
    before = {"model_caps": {m: c.amount for m, c in mine.items()}}
    after = {"model_caps": new}
    ctx.audit(
        "employee", upn, before, after, role, group_id=group_id, department_id=group.department_id
    )
    await ctx.enqueue("employee", upn, "/team/member_update")
    return [{"entity": "employee", "id": upn, "values": after}]


async def set_limits(
    ctx: WriteContext,
    principal: Principal,
    directory: Directory,
    group_id: int,
    upn: str,
    body: MemberLimitsIn,
) -> list[dict]:
    """FR-G4, D7: concurrency and rpm within the policy range; admins may exceed the maximum."""
    await can_write_members(ctx.db, principal, group_id)
    group = await ctx.db.get(Grp, group_id)
    employee, tier = await _target(ctx, directory, group, upn)
    role = authorize(principal, employee, group, tier)
    for field, policy_key in (("max_parallel", "concurrency"), ("rpm_limit", "rpm")):
        value, policy = getattr(body, field), await ctx.db.get(Policy, policy_key)
        if value is not None and policy:
            if err := check_limit(field, value, policy.value, principal.is_admin):
                raise err

    before = {
        "max_parallel": employee.max_parallel, "rpm_limit": employee.rpm_limit,
        "model_tpm_limits": employee.model_tpm_limits,
    }  # fmt: skip
    employee.max_parallel, employee.rpm_limit = body.max_parallel, body.rpm_limit
    employee.model_tpm_limits = body.model_tpm_limits or None
    after = {
        "max_parallel": employee.max_parallel, "rpm_limit": employee.rpm_limit,
        "model_tpm_limits": employee.model_tpm_limits,
    }  # fmt: skip
    ctx.audit(
        "employee", upn, before, after, role, group_id=group_id, department_id=group.department_id
    )
    await ctx.enqueue("employee", upn, "/key/update")
    return [{"entity": "employee", "id": upn, "values": after}]


async def set_defaults(
    ctx: WriteContext, principal: Principal, group_id: int, body: MemberDefaultsIn
) -> list[dict]:
    """FR-G2: defaults for new members; the model caps live in the policy table (see PROGRESS)."""
    role = await can_edit_member_defaults(ctx.db, principal, group_id)
    group = await ctx.db.get(Grp, group_id)
    key = f"member_defaults.group.{group_id}"
    row = await ctx.db.get(Policy, key)
    caps = {m: a for m, a in body.model_caps.items() if a > 0}
    old_caps = row.value.get("model_caps", {}) if row else {}
    before = {"cap": group.default_member_cap, "model_caps": old_caps}
    group.default_member_cap = body.cap
    value = {"model_caps": {m: float(a) for m, a in caps.items()}}
    if row:
        row.value = value
    else:
        ctx.db.add(Policy(key=key, value=value))
    after = {"cap": body.cap, "model_caps": caps}
    ctx.audit(
        "group", group_id, before, after, role, group_id=group_id, department_id=group.department_id
    )
    await ctx.enqueue("group", group_id, "/team/update")
    return [{"entity": "group", "id": group_id, "values": after}]
