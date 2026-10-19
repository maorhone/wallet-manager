from sqlalchemy import select

from app.db import LockKind
from app.models import DepartmentModelAllowance, GroupModelPot, Grp
from app.permissions import Principal, can_edit_group_pots_limits
from app.schemas.departments import ModelAmounts
from app.schemas.groups import GroupLimitsIn
from app.services.validation import check_model_sums
from app.writes import WriteContext


async def set_model_pots(
    ctx: WriteContext, principal: Principal, group_id: int, body: ModelAmounts
) -> list[dict]:
    """FR-D3: Σ group pots for a model must fit the department's allowance for it."""
    role = await can_edit_group_pots_limits(ctx.db, principal, group_id)
    dept_id = (await ctx.db.get(Grp, group_id)).department_id
    await ctx.lock(LockKind.DEPARTMENT, dept_id)
    new = {m: a for m, a in body.items() if a > 0}
    pots = list(
        await ctx.db.scalars(
            select(GroupModelPot)
            .join(Grp, Grp.id == GroupModelPot.group_id)
            .where(Grp.department_id == dept_id, Grp.status != "archived")
        )
    )
    others: dict[int, dict] = {}
    for p in pots:
        if p.group_id != group_id and p.model in new:
            others.setdefault(p.group_id, {})[p.model] = p.amount
    allowance = {
        r.model: r.amount
        for r in await ctx.db.scalars(
            select(DepartmentModelAllowance).where(
                DepartmentModelAllowance.department_id == dept_id
            )
        )
    }
    if err := check_model_sums({**others, group_id: new}, allowance):
        raise err

    mine = {p.model: p for p in pots if p.group_id == group_id}
    for model, row in mine.items():
        if model in new:
            row.amount = new[model]
        else:
            await ctx.db.delete(row)
    for model, amount in new.items():
        if model not in mine:
            ctx.db.add(GroupModelPot(group_id=group_id, model=model, amount=amount))
    before = {"model_pots": {m: p.amount for m, p in mine.items()}}
    after = {"model_pots": new}
    ctx.audit("group", group_id, before, after, role, group_id=group_id, department_id=dept_id)
    await ctx.enqueue("group", group_id, "/team/update")
    return [{"entity": "group", "id": group_id, "values": after}]


async def set_limits(
    ctx: WriteContext, principal: Principal, group_id: int, body: GroupLimitsIn
) -> list[dict]:
    """FR-D4: team tpm and per-model tpm limits; no sum rule."""
    role = await can_edit_group_pots_limits(ctx.db, principal, group_id)
    group = await ctx.db.get(Grp, group_id)
    before = {"tpm_limit": group.tpm_limit, "model_tpm_limits": group.model_tpm_limits}
    group.tpm_limit = body.tpm_limit
    group.model_tpm_limits = body.model_tpm_limits or None
    after = {"tpm_limit": group.tpm_limit, "model_tpm_limits": group.model_tpm_limits}
    ctx.audit(
        "group", group_id, before, after, role, group_id=group_id, department_id=group.department_id
    )
    await ctx.enqueue("group", group_id, "/team/update")
    return [{"entity": "group", "id": group_id, "values": after}]
