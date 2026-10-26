"""Section budget overview and Action Center (5.17, WF-01): read-only, built on group_summary."""

from decimal import Decimal

from sqlalchemy import func, select

from app.config import Settings
from app.models import (
    AuditLog,
    Department,
    Employee,
    Grp,
    Initiative,
    Policy,
    ProductionSystem,
    Request,
    ServiceKey,
)
from app.permissions import ADMIN, ORG_ALLOCATOR, REP, can_view_department
from app.services import requests as requests_service
from app.services import views
from app.services.rate_card import metered_value
from app.services.spend import Spend

DEFAULT_SHORTAGE = Decimal("0.85")


def _group_id(scope: str) -> int:
    kind, _, raw = scope.partition(":")
    if kind != "group" or not raw.isdigit():
        raise views.invalid_request("scope must be group:<id>")
    return int(raw)


async def overview(db, principal, settings: Settings, scope: str, raw_period: str | None) -> dict:
    if scope.startswith("department:"):
        return await department_overview(db, principal, settings, scope, raw_period)
    group_id = _group_id(scope)
    s = await views.group_summary(db, principal, settings, group_id, raw_period)
    spend = await Spend.load(db, s["period"])
    w = s["wallet"]
    rows = []
    group = await db.get(Grp, group_id)
    th = await views._thresholds(db)
    g = await views._Group.load(db, group, spend, views.period_ctx(settings, raw_period)["at"], th)
    if "top_consumers" in s and s["actions"]["edit_members"]:
        rows += [
            {"kind": "employee", "id": r["upn"], "name": r["display_name"], "total": r["total"],
             "used": r["used"], "percent": r["percent"], "state": r["state"]}
            for r in g.rows(spend, th)
        ]  # fmt: skip
    at = views.period_ctx(settings, raw_period)["at"]
    parents = await views._parent_map(db, group.department_id, at)
    for child in await db.scalars(
        select(Grp).where(
            Grp.id.in_([g for g, p in parents.items() if p == group_id]), Grp.status != "archived"
        )
    ):
        # A child's row carries its whole subtree, like the headline (D-055, r6 B3).
        subtree = [child.id, *await views._descendants(db, child.id, at)]
        used = sum((spend.used("group", i) for i in subtree), Decimal(0))
        rows.append(
            {"kind": "group", "id": str(child.id), "name": child.name, "total": child.base_budget,
             "used": used, "percent": _pct(used, child.base_budget), "state": None}
        )  # fmt: skip
    for ini in await db.scalars(
        select(Initiative).where(Initiative.group_id == group_id, Initiative.status == "active")
    ):
        used = await initiative_used(db, spend, ini.id, s["period"])
        rows.append(
            {"kind": "initiative", "id": str(ini.id), "name": ini.name, "total": ini.budget,
             "used": used, "percent": _pct(used, ini.budget), "state": None}
        )  # fmt: skip
    used = w["rollup_used"]  # D-055: descendants' consumption counts once, in the parent's view
    remaining = w["total"] - used
    return {
        "period": s["period"], "reset_date": s["reset_date"], "spend_taken_at": s["spend_taken_at"],
        "group": s["group"],
        "kpis": {
            "total": w["total"], "allocated": w["allocated"], "used": used,
            "remaining": remaining,
            "metered_value": await metered_value(db, "group", group_id, s["period"]),
        },
        "bar": _bar(used, w["allocated"], w["total"]),
        "rows": rows,
        "actions": s["actions"],
    }  # fmt: skip


async def initiative_used(db, spend: Spend, initiative_id: int, period: str) -> Decimal:
    """No initiative snapshot scope exists: sum its keys (revoked/rotated generations included,
    and keys moved to a system for the periods up to the move, r6 B3)."""
    key_ids = [
        *await db.scalars(
            select(ServiceKey.id).where(
                ServiceKey.owner_kind == "initiative", ServiceKey.owner_id == initiative_id
            )
        ),
        *await _moved_key_ids(db, initiative_id, period),
    ]
    return sum((spend.used("service_key", k) for k in key_ids), Decimal(0))


async def _moved_key_ids(db, initiative_id: int, period: str) -> list[int]:
    """Keys moved out of the initiative whose move happened in ``period`` or later."""
    ids = []
    for a in await db.scalars(
        select(AuditLog).where(
            AuditLog.entity == "service_key",
            AuditLog.before["owner_kind"].as_string() == "initiative",
            AuditLog.before["owner_id"].as_string() == str(initiative_id),
        )
    ):
        if a.after and a.after.get("owner_kind") == "system" and a.at.strftime("%Y-%m") >= period:
            ids.append(int(a.entity_id))
    return ids


def _bar(used: Decimal, allocated: Decimal, total: Decimal) -> dict:
    """D-055: used + allocated_unused + unallocated == total, also when spend came from a
    revoked allocation (it is in ``used`` but no longer in ``allocated``)."""
    remaining = max(total - used, Decimal(0))
    unused = min(max(allocated - used, Decimal(0)), remaining)
    return {"used": used, "allocated_unused": unused, "unallocated": remaining - unused,
            "total": total}  # fmt: skip


def _pct(used: Decimal, total: Decimal) -> float:
    return float(used / total) if total else 0.0


async def attention(db, principal, settings: Settings, scope: str, raw_period: str | None) -> dict:
    group_id = _group_id(scope)
    o = await overview(db, principal, settings, scope, raw_period)
    row = await db.get(Policy, "shortage_threshold")
    threshold = Decimal(str(row.value)) if row and row.value else DEFAULT_SHORTAGE
    pending = [
        r
        for r in await db.scalars(
            select(Request)
            .where(
                Request.scope_kind == "group",
                Request.scope_id == group_id,
                Request.status.in_(("submitted", "forwarded")),
            )
            .order_by(Request.id)
        )
        if await requests_service.can_see(db, principal, r)
    ]
    s = await views.group_summary(db, principal, settings, group_id, raw_period)
    risks = [
        {"kind": r["kind"], "id": r["id"], "name": r["name"], "percent": r["percent"]}
        for r in o["rows"]
        if r["total"] and Decimal(str(r["percent"])) >= threshold
    ]
    credit_requests = [r for r in pending if r.type == "credits"]
    opportunities = []
    if credit_requests:
        opportunities = [
            {"grant_id": x["id"], "unused": x["amount"] - x["used"], "model": x["model"]}
            for x in s["grants"]
            if x["amount"] - x["used"] > 0
        ]
    return {
        "requests": [
            {"id": r.id, "type": r.type, "status": r.status, "requester_upn": r.requester_upn,
             "amount": r.amount}
            for r in pending
        ],
        "shortage_risks": risks,
        "reallocation_opportunities": opportunities,
        "threshold": threshold,
    }  # fmt: skip


def _previous(period: str) -> str:
    year, month = map(int, period.split("-"))
    return f"{year - (month == 1)}-{(month - 2) % 12 + 1:02d}"


async def department_overview(
    db, principal, settings: Settings, scope: str, raw_period: str | None
) -> dict:
    """WF-02: department KPIs, section comparison (per-member denominators), systems, tree.

    Org Systems are listed apart and never enter the department totals (D-076)."""
    kind, _, raw = scope.partition(":")
    if not raw.isdigit():
        raise views.invalid_request("scope must be department:<id>")
    dept_id = int(raw)
    pc = views.period_ctx(settings, raw_period)
    await can_view_department(db, principal, dept_id)
    dept = await db.get(Department, dept_id)
    th = await views._thresholds(db)
    spend = await Spend.load(db, pc["period"])
    before = await Spend.load(db, _previous(pc["period"]))
    d = await views._dept(db, dept, spend, pc["at"], th)
    card = d["card"]
    groups = d["groups"]
    sections = []
    parents = await views._parent_map(db, dept_id, pc["at"])
    for g in (x for x in groups if parents.get(x.id) is None):
        ids = [g.id, *await views._descendants(db, g.id, pc["at"])]
        members = await db.scalar(
            select(func.count()).where(Employee.group_id.in_(ids), Employee.status != "left")
        )
        used = sum((spend.used("group", i) for i in ids), Decimal(0))
        prev = sum((before.used("group", i) for i in ids), Decimal(0))
        base = d["bases"][g.id]
        sections.append(
            {"id": g.id, "name": g.name, "total": base, "used": used, "percent": _pct(used, base),
             "members": members, "used_per_member": used / members if members else None,
             "previous_used": prev}
        )  # fmt: skip

    async def systems(*where):
        out = []
        for x in await db.scalars(
            select(ProductionSystem)
            .where(ProductionSystem.status == "active", *where)
            .order_by(ProductionSystem.id)
        ):
            out.append({"id": x.id, "name": x.name, "budget": x.budget,
                        "used": spend.used("system", x.id)})  # fmt: skip
        return out

    return {
        "period": pc["period"], "reset_date": pc["reset_date"], "department": dept.name,
        "kpis": {"total": card["total"], "allocated": card["allocated"], "used": card["used"],
                 "remaining": card["remaining"]},
        "bar": _bar(card["used"], card["allocated"], card["total"]),
        "previous_period": {"period": _previous(pc["period"]),
                            "used": before.used("department", dept_id)},
        "sections": sections,
        "tree": [{"id": g.id, "name": g.name, "parent_group_id": parents.get(g.id)}
                 for g in groups],
        "internal_systems": await systems(
            ProductionSystem.funding == "department", ProductionSystem.department_id == dept_id
        ),
        "org_systems": await systems(
            ProductionSystem.funding == "organization",
            ProductionSystem.responsible_department_id == dept_id,
        ),
        "actions": {
            "edit_hierarchy": bool(
                principal.department_roles(dept_id) & {ADMIN, REP, ORG_ALLOCATOR}
            )
        },
    }  # fmt: skip
