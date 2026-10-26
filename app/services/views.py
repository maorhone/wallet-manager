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
    ProductionSystem,
)
from app.outbox.desired import contributing_grants
from app.permissions import (
    ADMIN,
    HEAD,
    REP,
    VIEWER,
    Principal,
    can_view_department,
    can_view_group,
    can_view_members,
    target_tier,
)
from app.services import wallet
from app.services.attribution import displayed_used
from app.services.history import values_as_of
from app.services.lifecycle import row_actions
from app.services.owners import holders, owner_of
from app.services.personal_values import authorize
from app.services.spend import Spend, month_bounds
from app.services.validation import initiative_allocated, key_allocated
from app.writes import sync_status

ZERO = Decimal(0)
CARD = ("total", "allocated", "unallocated", "used", "remaining", "percent", "state")


def period_ctx(settings: Settings, raw: str | None, now: datetime | None = None) -> dict:
    """Validate ``YYYY-MM`` (default: the current month in APP_TIMEZONE) and describe it."""
    tz = ZoneInfo(settings.app_timezone)
    current = (now.astimezone(tz) if now else datetime.now(tz)).strftime("%Y-%m")
    period = raw or current
    try:
        parsed = datetime.strptime(period, "%Y-%m")
        if parsed.strftime("%Y-%m") != period:
            raise ValueError
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


async def _grants_by_group(
    db, group_ids: list[int], timezone: str = "Asia/Jerusalem", period: str | None = None
) -> dict[int, Decimal]:
    rows = await db.execute(
        select(Grant.group_id, func.sum(Grant.amount - Grant.returned))
        .where(contributing_grants(timezone, period), Grant.group_id.in_(group_ids))
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
    parents = await _parent_map(db, dept.id, at)
    internal_ids = await db.scalars(
        select(ProductionSystem.id).where(
            ProductionSystem.department_id == dept.id,
            ProductionSystem.funding == "department",
        )
    )
    used = spend.used("department", dept.id) + sum(
        (spend.used("system", system_id) for system_id in internal_ids), ZERO
    )
    card = wallet.department_wallet(
        values["budget"], sum((bases[g.id] for g in groups if parents.get(g.id) is None), ZERO),
        # D-076: internal systems are carved from the department envelope, like the reserve
        values["pmo_pool"], values["reserve"] + await _internal_as_of(db, dept.id, at),
        used,
    )  # fmt: skip
    card["state"] = wallet.state(
        card["total"], card["used"], card["remaining"], [], card["allocated"], **th
    )
    return {"card": card, "groups": groups, "bases": bases, "values": values}


async def _parent_map(db, department_id: int, at) -> dict[int, int | None]:
    """Group id -> parent id for the department's tree as it stood at ``at`` (audit history)."""
    groups = list(await db.scalars(select(Grp).where(Grp.department_id == department_id)))
    live = {str(g.id): {"parent_group_id": g.parent_group_id} for g in groups}
    past = await values_as_of(db, "group", live, at)
    return {int(k): v["parent_group_id"] for k, v in past.items()}


async def _internal_as_of(db, department_id: int, at) -> Decimal:
    """D-076 internal-system carve as it stood at ``at``."""
    q = select(ProductionSystem).where(
        ProductionSystem.department_id == department_id,
        ProductionSystem.funding == "department",
        ProductionSystem.status == "active",
    )
    if at is not None:
        q = q.where(ProductionSystem.created_at <= at)
    systems = list(await db.scalars(q))
    past = await values_as_of(
        db, "production_system", {str(x.id): {"budget": x.budget} for x in systems}, at
    )
    return sum((v["budget"] for v in past.values()), ZERO)


async def _descendants(db, group_id: int, at=None) -> list[int]:
    """Ids below ``group_id`` in the same department tree (each node once), as of ``at``."""
    dept = await db.scalar(select(Grp.department_id).where(Grp.id == group_id))
    parents = await _parent_map(db, dept, at)
    out, level = [], [group_id]
    while level:
        level = [g for g, p in parents.items() if p in level]
        out += level
    return out


class _Group:
    """One group's wallet and its members' values for a period."""

    def __init__(self, group, dept, members, values, shares, card, dept_card):
        self.group, self.dept, self.members = group, dept, members
        self.values, self.shares, self.card, self.dept_card = values, shares, card, dept_card

    @classmethod
    async def load(
        cls, db, group: Grp, spend: Spend, at, th, timezone: str = "Asia/Jerusalem"
    ) -> "_Group":
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
        shares: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for _, upn, amount in await _shares(db, [group.id]):
            shares[upn] += amount
        grants = (await _grants_by_group(db, [group.id], timezone, spend.period)).get(
            group.id, ZERO
        )
        active = [m.upn for m in members if m.status != "left"]
        keys_budget = await key_allocated(db, "group", group.id) + await initiative_allocated(
            db, group.id
        )
        parents = await _parent_map(db, group.department_id, at)
        children = list(
            await db.scalars(
                select(Grp).where(
                    Grp.id.in_([g for g, p in parents.items() if p == group.id]),
                    Grp.status != "archived",
                )
            )
        )
        child_bases = await values_as_of(
            db, "group", {str(c.id): {"base_budget": c.base_budget} for c in children}, at
        )
        carved = sum((v["base_budget"] for v in child_bases.values()), ZERO)  # D-055: once, here
        card = wallet.group_wallet(
            base, grants, sum((values[u]["base_cap"] for u in active), ZERO) + keys_budget + carved,
            sum((shares.get(u, ZERO) for u in active), ZERO), spend.used("group", group.id),
        )  # fmt: skip
        card["base"], card["grants"] = base, grants
        card["rollup_used"] = card["used"] + sum(
            (spend.used("group", d) for d in await _descendants(db, group.id, at)), ZERO
        )
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
    db: AsyncSession, directory: Directory, settings: Settings, principal: Principal, raw,
    now: datetime,
) -> dict:  # fmt: skip
    emp = await db.get(Employee, principal.upn)
    group = await db.get(Grp, emp.group_id) if emp and emp.group_id else None
    if group is None:
        raise ApiError(404, "NOT_FOUND", "Wallet not found")
    pc, th = period_ctx(settings, raw), await _thresholds(db)
    spend = await Spend.load(db, pc["period"])
    g = await _Group.load(db, group, spend, pc["at"], th, settings.app_timezone)
    row = next(r for r in g.rows(spend, th) if r["upn"] == emp.upn)
    share_rows = await db.execute(
        select(GrantShare, Grant)
        .join(Grant, Grant.id == GrantShare.grant_id)
        .where(GrantShare.upn == emp.upn, GrantShare.status == "active")
        .order_by(GrantShare.expires_at, GrantShare.id)
    )
    share_used = await displayed_used(
        db,
        emp.upn,
        emp.base_cap,
        spend.used("employee", emp.upn),
        spend.period,
        settings.app_timezone,
    )
    shares = [
        {
            "id": s.id, "share_id": s.id, "grant_id": s.grant_id, "project_ref": g.project_ref,
            "model": g.model, "amount": s.amount, "used": share_used[s.id],
            "expires_at": s.expires_at, "days_left": max(0, (s.expires_at - now).days),
        }
        for s, g in share_rows
    ]  # fmt: skip
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
    owner = await owner_of(db, directory, emp.upn, group, principal=principal)
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
    g = await _Group.load(db, group, spend, pc["at"], th, settings.app_timezone)
    heads = {p["upn"] for p in await holders(db, directory, HEAD, "group", group_id)}
    syncs = await sync_status(db, "employee", [m.upn for m in g.members])
    members = {m.upn: m for m in g.members}
    cache: dict = {}
    rows = []
    for r in g.rows(spend, th):
        if members[r["upn"]].status == "left":
            continue  # a deleted employee leaves the table (F10) but stays in history
        editable = False
        tier = await target_tier(db, directory, r["upn"])
        if not pc["read_only"]:
            try:
                authorize(principal, members[r["upn"]], group, tier)
                editable = True
            except ApiError:
                pass
        rows.append(
            {
                **r,
                "model_tpm_limits": members[r["upn"]].model_tpm_limits or {},
                "is_group_head": r["upn"] in heads,
                "sync_status": syncs[r["upn"]],
                "editable": editable,
                "actions": row_actions(principal, group, members[r["upn"]], tier),
                "owner": await owner_of(db, directory, r["upn"], group, cache),
            }  # fmt: skip
        )
    return {**_envelope(pc, spend), "members": rows}


async def group_model_pots(db, group_id: int, timezone: str, period: str) -> dict[str, Decimal]:
    """Effective model pots shared by the summary and allocation report."""
    pots = {
        p.model: p.amount
        for p in await db.scalars(select(GroupModelPot).where(GroupModelPot.group_id == group_id))
    }
    contributions = list(
        await db.scalars(
            select(Grant).where(Grant.group_id == group_id, contributing_grants(timezone, period))
        )
    )
    for grant in contributions:
        if grant.model:
            pots[grant.model] = pots.get(grant.model, ZERO) + grant.amount - grant.returned
    return pots


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
    g = await _Group.load(db, group, spend, pc["at"], th, settings.app_timezone)
    pots = await group_model_pots(db, group_id, settings.app_timezone, spend.period)
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
        "group": {
            "id": group.id,
            "department_id": group.department_id,
            "name": group.name,
            "default_member_cap": group.default_member_cap,
        },  # fmt: skip
        "wallet": {k: g.card[k] for k in (*CARD, "base", "grants", "rollup_used")},
        "pots": [
            {
                "model": model,
                "amount": amount,
                "used": used_by_model.get(model, ZERO),
                "members_caps_sum": caps_sum.get(model, ZERO),
            }
            for model, amount in sorted(pots.items())
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
            "service_keys": {
                "view": bool(roles & {ADMIN, REP, HEAD, VIEWER}),
                "manage": bool(roles & {ADMIN, HEAD}),
            },
        },
    }


async def _group_heads(
    db: AsyncSession, directory: Directory, principal: Principal, settings: Settings,
    groups: list[Grp], spend: Spend, pc: dict, th,
) -> list[dict]:  # fmt: skip
    """Personal values of each group head (DR9, §12.5.4 tab 4) and the actor's rights on them."""
    with_grant = set(
        await db.scalars(
            select(Grant.group_id).where(
                Grant.group_id.in_([g.id for g in groups]), Grant.status == "active"
            )
        )
    )
    out = []
    if not principal.department_roles(groups[0].department_id) & {ADMIN, REP} if groups else True:
        return out  # viewers and PMO see aggregates only (decision 7)
    # Heads keep their AD role when moved, so match them across the whole department.
    heads = set()
    for group in groups:
        heads |= {p["upn"] for p in await holders(db, directory, HEAD, "group", group.id)}
    for group in groups:
        upns = heads
        if not upns:
            continue
        g = await _Group.load(db, group, spend, pc["at"], th, settings.app_timezone)
        syncs = await sync_status(db, "employee", sorted(upns))
        members = {m.upn: m for m in g.members}
        for r in g.rows(spend, th):
            if r["upn"] not in upns or members[r["upn"]].status == "left":
                continue
            edit = False
            if not pc["read_only"]:
                try:
                    authorize(
                        principal,
                        members[r["upn"]],
                        group,
                        await target_tier(db, directory, r["upn"]),
                    )
                    edit = True
                except ApiError:
                    pass
            out.append(
                {
                    "upn": r["upn"], "display_name": r["display_name"], "group_id": group.id,
                    "group_name": group.name, "base_cap": r["base_cap"],
                    "shares": r["shares_total"], "model_caps": r["model_caps"],
                    "max_parallel": r["max_parallel"], "rpm_limit": r["rpm_limit"],
                    "model_tpm_limits": members[r["upn"]].model_tpm_limits or {},
                    "sync_status": syncs[r["upn"]],
                    "actions": {"edit": edit, "share": edit and group.id in with_grant},
                }
            )  # fmt: skip
    return out


async def department_summary(
    db: AsyncSession,
    directory: Directory,
    principal: Principal,
    settings: Settings,
    department_id: int,
    raw: str | None,
) -> dict:
    await can_view_department(db, principal, department_id)
    dept = await db.get(Department, department_id)
    pc, th = period_ctx(settings, raw), await _thresholds(db)
    spend = await Spend.load(db, pc["period"])
    d = await _dept(db, dept, spend, pc["at"], th)
    card, ids = d["card"], [g.id for g in d["groups"]]
    grants = await _grants_by_group(db, ids, settings.app_timezone, spend.period)
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

    group_sync = await sync_status(db, "group", [str(i) for i in ids])
    dept_sync = (await sync_status(db, "department", [str(department_id)]))[str(department_id)]
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
                "overalloc_factor": g.overalloc_factor,
                "base": base,
                "grants": gr,
                "total": w["total"],
                "used": used,
                "percent": w["percent"],
                "sync_status": group_sync[str(g.id)],
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
    heads = await _group_heads(db, directory, principal, settings, d["groups"], spend, pc, th)
    return {
        **_envelope(pc, spend),
        "department": {"id": dept.id, "name": dept.name},
        "group_heads": heads,
        "wallet": {k: card[k] for k in CARD},
        "split": {
            "groups": [{k: r[k] for k in ("id", "name", "kind", "base")} for r in table],
            "pmo_pool": d["values"]["pmo_pool"],
            "reserve": d["values"]["reserve"],
            "unallocated": card["unallocated"],
            "pmo_reserve_sync_status": dept_sync,
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
            "place": bool(roles & {ADMIN, REP}),
            "view_production": bool(roles & {ADMIN, REP}),
            "edit_production": ADMIN in roles,
        },
    }
