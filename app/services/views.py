"""Read models (SPEC §11.1, §12.3, §12.4): wallet card, group and department summaries, members."""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.directory import Directory
from app.errors import ApiError, invalid_request
from app.models import (
    Department,
    DepartmentModelAllowance,
    Employee,
    EmployeeModelCap,
    Grant,
    GrantShare,
    GroupModelPot,
    Grp,
    Policy,
)
from app.permissions import (
    ADMIN,
    HEAD,
    REP,
    Principal,
    can_view_department,
    can_view_group,
    can_view_members,
    target_tier,
)
from app.services import wallet
from app.services.history import values_as_of
from app.services.owners import holders, owner_of
from app.services.personal_values import authorize
from app.services.spend import Spend, month_bounds
from app.writes import sync_status

ZERO = Decimal(0)
CARD = ("total", "allocated", "unallocated", "used", "remaining", "percent", "state")


def period_ctx(settings: Settings, raw: str | None) -> dict:
    """Validate ``YYYY-MM`` (default: the current month in APP_TIMEZONE) and describe it."""
    tz = ZoneInfo(settings.app_timezone)
    current = datetime.now(tz).strftime("%Y-%m")
    period = raw or current
    try:
        datetime.strptime(period, "%Y-%m")
    except ValueError:
        raise invalid_request("period must be YYYY-MM") from None
    if period > current:
        raise invalid_request("period is in the future")
    _, end = month_bounds(period, tz)
    past = period < current
    return {
        "period": period,
        "reset_date": end.astimezone(tz).date().isoformat(),
        "read_only": past,
        "at": end if past else None,  # audit history cut-off
    }


def _envelope(pc: dict, spend: Spend) -> dict:
    return {
        "period": pc["period"], "reset_date": pc["reset_date"],
        "spend_taken_at": spend.taken_at, "read_only": pc["read_only"],
    }  # fmt: skip


async def _thresholds(db: AsyncSession) -> dict[str, Decimal]:
    row = await db.get(Policy, "thresholds")
    value = {"near": 0.8, "full": 1.0, **(row.value if row else {})}
    return {"near": Decimal(str(value["near"])), "full": Decimal(str(value["full"]))}


async def _grants_by_group(db, group_ids: list[int]) -> dict[int, Decimal]:
    rows = await db.execute(
        select(Grant.group_id, func.sum(Grant.amount))
        .where(Grant.status == "active", Grant.group_id.in_(group_ids))
        .group_by(Grant.group_id)
    )
    return dict(rows.all())


async def _shares(db, group_ids: list[int]) -> list[tuple[int, str, Decimal]]:
    """Active shares as (group_id, upn, amount)."""
    rows = await db.execute(
        select(Grant.group_id, GrantShare.upn, GrantShare.amount)
        .join(Grant, Grant.id == GrantShare.grant_id)
        .where(GrantShare.status == "active", Grant.group_id.in_(group_ids))
    )
    return [tuple(r) for r in rows.all()]


async def _dept(db, dept: Department, spend: Spend, at, th) -> dict:
    """Department wallet card, its (non-archived) groups and their base budgets as of ``at``."""
    groups = list(
        await db.scalars(select(Grp).where(Grp.department_id == dept.id, Grp.status != "archived"))
    )
    live = {"budget": dept.budget, "pmo_pool": dept.pmo_pool, "reserve": dept.reserve}
    values = (await values_as_of(db, "department", {str(dept.id): live}, at))[str(dept.id)]
    bases = await values_as_of(
        db, "group", {str(g.id): {"base_budget": g.base_budget} for g in groups}, at
    )
    bases = {int(k): v["base_budget"] for k, v in bases.items()}
    card = wallet.department_wallet(
        values["budget"], sum(bases.values(), ZERO), values["pmo_pool"], values["reserve"],
        spend.used("department", dept.id),
    )  # fmt: skip
    card["state"] = wallet.state(
        card["total"], card["used"], card["remaining"], [], card["allocated"], **th
    )
    return {"card": card, "groups": groups, "bases": bases, "values": values}


class _Group:
    """One group's wallet and its members' values for a period."""

    def __init__(self, group, dept, members, values, shares, card, dept_card):
        self.group, self.dept, self.members = group, dept, members
        self.values, self.shares, self.card, self.dept_card = values, shares, card, dept_card

    @classmethod
    async def load(cls, db, group: Grp, spend: Spend, at, th) -> "_Group":
        dept = await db.get(Department, group.department_id)
        dept_card = (await _dept(db, dept, spend, at, th))["card"]
        members = list(
            await db.scalars(
                select(Employee).where(Employee.group_id == group.id).order_by(Employee.upn)
            )
        )
        caps: dict[str, dict] = defaultdict(dict)
        cap_rows = select(EmployeeModelCap).where(
            EmployeeModelCap.upn.in_([m.upn for m in members])
        )
        for c in await db.scalars(cap_rows):
            caps[c.upn][c.model] = c.amount
        live = {
            m.upn: {
                "base_cap": m.base_cap, "model_caps": caps[m.upn],
                "max_parallel": m.max_parallel, "rpm_limit": m.rpm_limit,
            }
            for m in members
        }  # fmt: skip
        values = await values_as_of(db, "employee", live, at)
        base_live = {str(group.id): {"base_budget": group.base_budget}}
        base = (await values_as_of(db, "group", base_live, at))[str(group.id)]["base_budget"]
        shares = {upn: amount for _, upn, amount in await _shares(db, [group.id])}
        grants = (await _grants_by_group(db, [group.id])).get(group.id, ZERO)
        active = [m.upn for m in members if m.status != "left"]
        card = wallet.group_wallet(
            base, grants, sum((values[u]["base_cap"] for u in active), ZERO),
            sum((shares.get(u, ZERO) for u in active), ZERO), spend.used("group", group.id),
        )  # fmt: skip
        card["base"], card["grants"] = base, grants
        card["state"] = wallet.state(
            card["total"], card["used"], card["remaining"],
            [("department", dept_card["remaining"])], card["allocated"], **th,
        )  # fmt: skip
        return cls(group, dept, members, values, shares, card, dept_card)

    def rows(self, spend: Spend, th) -> list[dict]:
        parents = [("group", self.card["remaining"]), ("department", self.dept_card["remaining"])]
        out = []
        for m in self.members:
            v, used = self.values[m.upn], spend.used("employee", m.upn)
            shares = self.shares.get(m.upn, ZERO)
            w = wallet.employee_wallet(v["base_cap"], shares, used)
            state = wallet.state(
                w["total"], used, w["remaining"], parents, None, status=m.status, **th
            )
            out.append(
                {
                    "upn": m.upn,
                    "display_name": m.display_name,
                    "base_cap": v["base_cap"],
                    "shares_total": shares,
                    "total": w["total"],
                    "used": used,
                    "percent": w["percent"],
                    "model_caps": v["model_caps"],
                    "max_parallel": v["max_parallel"],
                    "rpm_limit": v["rpm_limit"],
                    "status": m.status,
                    "state": state,
                }  # fmt: skip
            )
        return out


async def me_wallet(
    db: AsyncSession, directory: Directory, settings: Settings, principal: Principal, raw
) -> dict:
    emp = await db.get(Employee, principal.upn)
    group = await db.get(Grp, emp.group_id) if emp and emp.group_id else None
    if group is None:
        raise ApiError(404, "NOT_FOUND", "Wallet not found")
    pc, th = period_ctx(settings, raw), await _thresholds(db)
    spend = await Spend.load(db, pc["period"])
    g = await _Group.load(db, group, spend, pc["at"], th)
    row = next(r for r in g.rows(spend, th) if r["upn"] == emp.upn)
    share_rows = await db.scalars(
        select(GrantShare).where(GrantShare.upn == emp.upn, GrantShare.status == "active")
    )
    shares = [
        {"id": s.id, "grant_id": s.grant_id, "amount": s.amount, "expires_at": s.expires_at}
        for s in share_rows
    ]
    caps, used_by_model = row["model_caps"], spend.by_model("employee", emp.upn)
    models = [
        {
            "model": m,
            "cap": caps.get(m),
            "used": used_by_model.get(m, ZERO),
            "remaining": caps[m] - used_by_model.get(m, ZERO) if m in caps else None,
        }  # fmt: skip
        for m in sorted(set(caps) | set(used_by_model))
    ]
    remaining = row["total"] - row["used"]
    g_rem, d_rem = g.card["remaining"], g.dept_card["remaining"]
    owner = await owner_of(db, directory, emp.upn, group)
    return {
        **_envelope(pc, spend),
        "sync_status": (await sync_status(db, "employee", [emp.upn]))[emp.upn],
        "wallet": {
            "total": row["total"],
            "base_cap": row["base_cap"],
            "shares_total": row["shares_total"],
            "used": row["used"],
            "remaining": remaining,
            "percent": row["percent"],
            "state": row["state"],
            "effective_remaining": wallet.effective_remaining(remaining, g_rem, d_rem),
            "binding": {
                "level": wallet.binding_level(remaining, g_rem, d_rem),
                "group_percent": g.card["percent"],
                "department_percent": g.dept_card["percent"],
            },
        },  # fmt: skip
        "models": models,
        "shares": shares,
        "limits": {
            "max_parallel": row["max_parallel"],
            "rpm_limit": row["rpm_limit"],
            "model_tpm_limits": emp.model_tpm_limits,
        },  # fmt: skip
        "group": {
            "id": group.id,
            "name": group.name,
            "percent": g.card["percent"],
            "state": g.card["state"],
        },  # fmt: skip
        "department": {"id": g.dept.id, "name": g.dept.name, "percent": g.dept_card["percent"]},
        "owners": {k: owner for k in ("base_cap", "model_caps", "limits", "shares")},
    }


async def members_list(
    db: AsyncSession,
    directory: Directory,
    settings: Settings,
    principal: Principal,
    group_id: int,
    raw: str | None,
) -> dict:
    await can_view_members(db, principal, group_id)
    group = await db.get(Grp, group_id)
    pc, th = period_ctx(settings, raw), await _thresholds(db)
    spend = await Spend.load(db, pc["period"])
    g = await _Group.load(db, group, spend, pc["at"], th)
    heads = {p["upn"] for p in await holders(db, directory, HEAD, "group", group_id)}
    syncs = await sync_status(db, "employee", [m.upn for m in g.members])
    members = {m.upn: m for m in g.members}
    cache: dict = {}
    rows = []
    for r in g.rows(spend, th):
        editable = False
        if not pc["read_only"]:
            try:
                authorize(
                    principal, members[r["upn"]], group, await target_tier(db, directory, r["upn"])
                )
                editable = True
            except ApiError:
                pass
        rows.append(
            {
                **r,
                "is_group_head": r["upn"] in heads,
                "sync_status": syncs[r["upn"]],
                "editable": editable,
                "owner": await owner_of(db, directory, r["upn"], group, cache),
            }  # fmt: skip
        )
    return {**_envelope(pc, spend), "members": rows}


async def group_summary(
    db: AsyncSession, principal: Principal, settings: Settings, group_id: int, raw: str | None
) -> dict:
    await can_view_group(db, principal, group_id)
    group = await db.get(Grp, group_id)
    try:
        await can_view_members(db, principal, group_id)
        see_members = True
    except ApiError:
        see_members = False  # D11: PMO and viewers get aggregates only
    pc, th = period_ctx(settings, raw), await _thresholds(db)
    spend = await Spend.load(db, pc["period"])
    g = await _Group.load(db, group, spend, pc["at"], th)

    pots = await db.scalars(select(GroupModelPot).where(GroupModelPot.group_id == group_id))
    caps_sum: dict[str, Decimal] = defaultdict(lambda: ZERO)
    caps = await db.execute(
        select(EmployeeModelCap.model, EmployeeModelCap.amount)
        .join(Employee, Employee.upn == EmployeeModelCap.upn)
        .where(Employee.group_id == group_id, Employee.status != "left")
    )
    for model, amount in caps.all():
        caps_sum[model] += amount
    used_by_model = spend.by_model("group", group_id)
    grants = await db.scalars(
        select(Grant).where(Grant.group_id == group_id, Grant.status == "active")
    )

    top, near = [], []
    if see_members:
        rows = g.rows(spend, th)
        slim = ("upn", "display_name", "total", "used", "percent", "state")
        by_use = sorted(rows, key=lambda r: -r["used"])
        top = [{k: r[k] for k in slim} for r in by_use[:5] if r["used"] > 0]
        near = [
            {k: r[k] for k in slim} for r in rows if r["state"] in ("near_limit", "blocked_own")
        ]
    pending = 0
    for entity, ids in (("group", [str(group_id)]), ("employee", [m.upn for m in g.members])):
        statuses = await sync_status(db, entity, ids)
        pending += sum(1 for s in statuses.values() if s != "applied")
    roles = principal.group_roles(group_id, group.department_id)
    return {
        **_envelope(pc, spend),
        "group": {"id": group.id, "name": group.name},
        "wallet": {k: g.card[k] for k in (*CARD, "base", "grants")},
        "pots": [
            {
                "model": p.model,
                "amount": p.amount,
                "used": used_by_model.get(p.model, ZERO),
                "members_caps_sum": caps_sum.get(p.model, ZERO),
            }
            for p in sorted(pots, key=lambda p: p.model)
        ],  # fmt: skip
        "grants": [
            {
                "id": x.id,
                "amount": x.amount,
                "model": x.model,
                "project_ref": x.project_ref,
                "expires_at": x.expires_at,
                "used": x.used,
            }
            for x in grants
        ],  # fmt: skip
        "top_consumers": top,
        "near_limit_members": near,
        "pending_sync_count": pending,
        "actions": {
            "edit_members": bool(roles & {ADMIN, REP, HEAD}),
            "edit_pots_limits": bool(roles & {ADMIN, REP}),
            "edit_defaults": bool(roles & {ADMIN, HEAD}),
        },
    }


async def department_summary(
    db: AsyncSession, principal: Principal, settings: Settings, department_id: int, raw: str | None
) -> dict:
    await can_view_department(db, principal, department_id)
    dept = await db.get(Department, department_id)
    pc, th = period_ctx(settings, raw), await _thresholds(db)
    spend = await Spend.load(db, pc["period"])
    d = await _dept(db, dept, spend, pc["at"], th)
    card, ids = d["card"], [g.id for g in d["groups"]]
    grants = await _grants_by_group(db, ids)
    member_caps = dict(
        (
            await db.execute(
                select(Employee.group_id, func.sum(Employee.base_cap))
                .where(Employee.group_id.in_(ids), Employee.status != "left")
                .group_by(Employee.group_id)
            )
        ).all()
    )
    shares: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for group_id, _, amount in await _shares(db, ids):
        shares[group_id] += amount

    table = []
    for g in d["groups"]:
        base, gr, used = d["bases"][g.id], grants.get(g.id, ZERO), spend.used("group", g.id)
        w = wallet.group_wallet(base, gr, member_caps.get(g.id, ZERO), shares[g.id], used)
        parents = [("department", card["remaining"])]
        table.append(
            {
                "id": g.id,
                "name": g.name,
                "kind": g.kind,
                "base": base,
                "grants": gr,
                "total": w["total"],
                "used": used,
                "percent": w["percent"],
                "state": wallet.state(
                    w["total"], used, w["remaining"], parents, w["allocated"], **th
                ),
            }  # fmt: skip
        )
    pots_sum: dict[str, Decimal] = defaultdict(lambda: ZERO)
    pots = await db.execute(
        select(GroupModelPot.model, GroupModelPot.amount).where(GroupModelPot.group_id.in_(ids))
    )
    for model, amount in pots.all():
        pots_sum[model] += amount
    allowances = await db.scalars(
        select(DepartmentModelAllowance)
        .where(DepartmentModelAllowance.department_id == department_id)
        .order_by(DepartmentModelAllowance.model)
    )
    roles = principal.department_roles(department_id)
    return {
        **_envelope(pc, spend),
        "department": {"id": dept.id, "name": dept.name},
        "wallet": {k: card[k] for k in CARD},
        "split": {
            "groups": [{k: r[k] for k in ("id", "name", "kind", "base")} for r in table],
            "pmo_pool": d["values"]["pmo_pool"],
            "reserve": d["values"]["reserve"],
            "unallocated": card["unallocated"],
        },
        "groups": table,
        "allowances": [
            {"model": a.model, "amount": a.amount, "pots_sum": pots_sum.get(a.model, ZERO)}
            for a in allowances
        ],
        "actions": {
            "edit_split": bool(roles & {ADMIN, REP}),
            "edit_budget": ADMIN in roles,
            "edit_allowances": ADMIN in roles,
        },
    }
