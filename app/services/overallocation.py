"""FR-V2 over-allocation findings: live LiteLLM values (§11.3) plus exact checks on WM tables."""

import logging
from collections import defaultdict
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics
from app.clock import Clock, SystemClock
from app.config import Settings
from app.directory import FixtureDirectory
from app.models import (
    Department,
    DepartmentModelAllowance,
    Employee,
    EmployeeModelCap,
    GroupModelPot,
    Grp,
    Initiative,
    ProductionSystem,
    ServiceKey,
)
from app.permissions import ADMIN, REP, VIEWER, Principal
from app.services.notifications import notify, reps_or_admins, upns_of
from app.services.owners import holders
from app.writes import plain

log = logging.getLogger(__name__)
ZERO = Decimal(0)


def _finding(source, level, entity_id, name, allocated, total, department_id, model=None) -> dict:
    return {
        "source": source, "level": level, "entity_id": entity_id, "name": name,
        "allocated": allocated, "total": total, "department_id": department_id, "model": model,
    }  # fmt: skip


async def _live(db: AsyncSession, reader, depts, groups) -> list[dict]:
    if reader is None:
        return []
    try:
        orgs, teams = await reader.org_allocations(), await reader.team_allocations()
        live_budget = await reader.team_budgets()
    except Exception:
        log.warning("over-allocation check: LiteLLM database unreachable; WM checks only")
        return []
    by_org = {d.litellm_org_id: d for d in depts}
    by_team = {g.litellm_team_id: g for g in groups}
    carved: dict[int, Decimal] = defaultdict(Decimal)
    child_carve: dict[str, Decimal] = defaultdict(Decimal)  # live parent team -> live children
    by_id = {g.id: g for g in groups}
    for g in groups:
        if g.parent_group_id in by_id and g.status != "archived":
            # D-055: child teams are carved from a parent. WM only names the hierarchy; the
            # amount is the child's live LiteLLM budget (a drifted WM value must not mask it).
            live = live_budget.get(g.litellm_team_id, ZERO)
            carved[g.department_id] += live
            child_carve[by_id[g.parent_group_id].litellm_team_id] += live
    out = []
    for o in orgs:
        d = by_org.get(o.organization_id)
        allocated = o.allocated - (carved[d.id] if d else ZERO)
        total = o.dept_budget - (d.reserve if d else ZERO)
        if allocated > total:
            out.append(
                _finding("litellm", "department", d.id if d else o.organization_id,
                         d.name if d else o.organization_alias, allocated, total,
                         d.id if d else None)
            )  # fmt: skip
    for t in teams:
        g = by_team.get(t.team_id)
        allocated = t.allocated + child_carve[t.team_id]  # members plus live child teams
        if allocated > t.group_budget:
            out.append(
                _finding("litellm", "group", g.id if g else t.team_id,
                         g.name if g else t.team_alias, allocated, t.group_budget,
                         g.department_id if g else None)
            )  # fmt: skip
    return out


async def _wm(db: AsyncSession, depts, groups) -> list[dict]:
    live_groups = [g for g in groups if g.status != "archived"]
    bases: dict[int, Decimal] = defaultdict(Decimal)
    for g in live_groups:
        if g.parent_group_id is None:  # child bases are carved from their parent (D-055)
            bases[g.department_id] += g.base_budget
    carved: dict[int, Decimal] = defaultdict(Decimal)
    for g in live_groups:
        if g.parent_group_id is not None:
            carved[g.parent_group_id] += g.base_budget
    internal = dict(
        (
            await db.execute(
                select(ProductionSystem.department_id, func.sum(ProductionSystem.budget))
                .where(
                    ProductionSystem.funding == "department", ProductionSystem.status == "active"
                )
                .group_by(ProductionSystem.department_id)
            )
        ).all()
    )
    out = []
    for d in depts:
        allocated = bases[d.id] + d.pmo_pool + d.reserve + internal.get(d.id, ZERO)
        if allocated > d.budget:
            out.append(_finding("wm", "department", d.id, d.name, allocated, d.budget, d.id))

    caps = dict(
        (
            await db.execute(
                select(Employee.group_id, func.sum(Employee.base_cap))
                .where(Employee.status != "left", Employee.group_id.is_not(None))
                .group_by(Employee.group_id)
            )
        ).all()
    )
    initiatives = dict(
        (
            await db.execute(
                select(Initiative.group_id, func.sum(Initiative.budget))
                .where(Initiative.status == "active")
                .group_by(Initiative.group_id)
            )
        ).all()
    )
    keys = dict(
        (
            await db.execute(
                select(ServiceKey.owner_id, func.sum(ServiceKey.max_budget))
                .where(
                    ServiceKey.owner_kind == "group",
                    ServiceKey.status.in_(("active", "pending")),
                )
                .group_by(ServiceKey.owner_id)
            )
        ).all()
    )
    for g in live_groups:
        total = g.base_budget * g.overalloc_factor
        allocated = (
            caps.get(g.id, ZERO) + carved[g.id] + initiatives.get(g.id, ZERO)
            + keys.get(g.id, ZERO)
        )  # fmt: skip
        if allocated > total:
            out.append(_finding("wm", "group", g.id, g.name, allocated, total, g.department_id))

    allowance = {
        (a.department_id, a.model): a.amount
        for a in await db.scalars(select(DepartmentModelAllowance))
    }
    pots = {(p.group_id, p.model): p.amount for p in await db.scalars(select(GroupModelPot))}
    by_id = {g.id: g for g in live_groups}
    dept_pots: dict[tuple, Decimal] = defaultdict(Decimal)
    for (gid, model), amount in pots.items():
        if gid in by_id:
            dept_pots[(by_id[gid].department_id, model)] += amount
    names = {d.id: d.name for d in depts}
    for (did, model), allocated in sorted(dept_pots.items()):
        total = allowance.get((did, model), ZERO)
        if allocated > total:
            out.append(_finding("wm", "group_model", did, names[did], allocated, total, did, model))

    member_caps = (
        await db.execute(
            select(Employee.group_id, EmployeeModelCap.model, func.sum(EmployeeModelCap.amount))
            .join(Employee, Employee.upn == EmployeeModelCap.upn)
            .where(Employee.status != "left", Employee.group_id.is_not(None))
            .group_by(Employee.group_id, EmployeeModelCap.model)
            .order_by(Employee.group_id, EmployeeModelCap.model)
        )
    ).all()
    for gid, model, allocated in member_caps:
        g = by_id.get(gid)
        total = pots.get((gid, model), ZERO) * (g.overalloc_factor if g else 1)
        if g and allocated > total:
            out.append(_finding("wm", "member_model", gid, g.name, allocated, total,
                                g.department_id, model))  # fmt: skip
    return out


async def findings(db: AsyncSession, reader) -> list[dict]:
    depts = list(await db.scalars(select(Department).order_by(Department.id)))
    groups = list(await db.scalars(select(Grp).order_by(Grp.id)))
    return await _live(db, reader, depts, groups) + await _wm(db, depts, groups)


async def scheduled_check(
    sf, reader, *, clock: Clock | None = None, settings: Settings | None = None, directory=None
) -> int:
    """FR-V2 findings and critical alerts, deduplicated per finding and local day."""
    settings = settings or Settings()
    directory = directory or FixtureDirectory(settings.seed_file)
    day = (clock or SystemClock()).now().astimezone(ZoneInfo(settings.app_timezone)).date()
    async with sf() as db:
        found = await findings(db, reader)
        admins = upns_of(await holders(db, directory, ADMIN, "global", None))
        reps = {}
        for f in found:
            did = f["department_id"]
            if did not in reps:
                reps[did] = await reps_or_admins(db, directory, did)
            scope_kind, scope_id = None, None
            if did is not None:
                scope_kind = "group" if f["level"] in ("group", "member_model") else "department"
                scope_id = f["entity_id"]
            await notify(
                db,
                admins | reps[did],
                "overallocation",
                scope_kind,
                scope_id,
                {**f, "day": day.isoformat()},
                f"overalloc:{f['level']}:{f['entity_id']}:{f['model']}:{day}",
                settings=settings,
            )
        await db.commit()
    for f in found:
        log.warning("over-allocation finding", extra={"finding": plain(f)})
    metrics.OVERALLOCATION.set(len(found))
    return len(found)


def visible_departments(principal: Principal) -> set[int] | None:
    """Department ids the principal may see in reports (§12.2); None means all, empty means none."""
    if principal.is_admin or principal.has(VIEWER, "global"):
        return None
    return {
        b.scope_id
        for b in principal.bindings + principal.inherited
        if b.role in (REP, VIEWER) and b.scope_kind == "department"
    }
