"""FR-R1/R2: snapshot allocation reports and visible grants grouped by project."""

from collections import defaultdict
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.errors import ApiError, invalid_request
from app.models import Department, DepartmentModelAllowance, Employee, Grp
from app.permissions import ADMIN, REP, VIEWER, can_view_department, can_view_members
from app.services import grants, views, wallet
from app.services.spend import Spend, month_bounds

ZERO = Decimal(0)
GRANT_VALUES = ("amount", "shared", "unshared", "used", "returned")


def _card(card):
    return {
        "total": card["total"], "allocated": card["allocated"], "spend": card["used"],
        "remaining": card["remaining"], "pct": card["percent"], "state": card["state"],
    }  # fmt: skip


def _models(allocations, spend, kind, id_):
    used = spend.by_model(kind, id_)
    return [
        {
            "model": model,
            "allocation": allocations.get(model),
            "spend": used.get(model, ZERO),
            "pct": wallet.percent(used.get(model, ZERO), allocations[model])
            if model in allocations
            else None,
        }
        for model in sorted(set(allocations) | set(used))
    ]


def _member(row, spend):
    card = wallet.employee_wallet(row["base_cap"], row["shares_total"], row["used"])
    return {
        "id": row["upn"], "name": row["display_name"] or row["upn"], "kind": "employee",
        **_card({**card, "state": row["state"]}),
        "base_cap": row["base_cap"], "shares_total": row["shares_total"],
        "model_caps": row["model_caps"],
        "models": _models(row["model_caps"], spend, "employee", row["upn"]),
    }  # fmt: skip


async def allocation(db, principal, settings, clock, level, id_, raw_period):
    """Authorize the requested level before loading child rows (D11)."""
    if level == "employee":
        entity = await db.get(Employee, id_)
        if entity is None or entity.group_id is None:
            raise ApiError(404, "NOT_FOUND", "Employee wallet not found")
        if principal.upn != entity.upn:
            await can_view_members(db, principal, entity.group_id)
    else:
        try:
            id_ = int(id_)
            if id_ <= 0 or id_ > 2**63 - 1:
                raise ValueError
        except ValueError:
            raise invalid_request("id must be a positive integer for this level") from None
        if level == "department":
            await can_view_department(db, principal, id_)
            if not principal.department_roles(id_) & {ADMIN, REP, VIEWER}:
                raise ApiError(403, "FORBIDDEN", "PMO reports are limited to grants")
            entity = await db.get(Department, id_)
        else:
            await can_view_members(db, principal, id_)
            entity = await db.get(Grp, id_)

    pc = views.period_ctx(settings, raw_period, now=clock.now())
    spend, th = await Spend.load(db, pc["period"]), await views._thresholds(db)
    if level == "department":
        card, rows, models = await _department(
            db, principal, settings, clock, entity, spend, pc, th
        )
    else:
        group = entity if level == "group" else await db.get(Grp, entity.group_id)
        g = await views._Group.load(db, group, spend, pc["at"], th, settings.app_timezone)
        members = [_member(r, spend) for r in g.rows(spend, th)]
        if level == "employee":
            member = next(r for r in members if r["id"] == entity.upn)
            card = {
                k: member[k] for k in ("total", "allocated", "spend", "remaining", "pct", "state")
            }
            rows, models = [], member["models"]
        else:
            card = _card(g.card)
            rows = [r for r, m in zip(members, g.members, strict=True) if m.status != "left"]
            pots = await views.group_model_pots(db, group.id, settings.app_timezone, spend.period)
            models = _models(pots, spend, "group", group.id)
    return {
        "level": level, "id": id_,
        "name": (entity.display_name or entity.upn) if level == "employee" else entity.name,
        "period": pc["period"], **card, "models": models, "rows": rows,
        "spend_taken_at": spend.taken_at,
    }  # fmt: skip


async def _department(db, principal, settings, clock, dept, spend, pc, th):
    d = await views._dept(db, dept, spend, pc["at"], th)
    rows = []
    for group in sorted(d["groups"], key=lambda g: g.id):
        g = await views._Group.load(db, group, spend, pc["at"], th, settings.app_timezone)
        rows.append(
            {
                "id": group.id,
                "name": group.name,
                "kind": "group",
                **_card(g.card),
                "base": g.card["base"],
                "grants": g.card["grants"],
            }
        )
    window = month_bounds(spend.period, ZoneInfo(settings.app_timezone))
    live, closed_used = await grants._reserved(db, dept.id, window)
    pool = await grants.pmo_pool(db, principal, settings, clock, dept.id, spend.period)
    pmo = wallet.pmo_wallet(d["values"]["pmo_pool"], live, closed_used, pool["used"])
    reserve = wallet.employee_wallet(d["values"]["reserve"], ZERO, ZERO)
    for id_, name, card in (("pmo_pool", "PMO pool", pmo), ("reserve", "Reserve", reserve)):
        card["state"] = wallet.state(
            card["total"], card["used"], card["remaining"], [], card["allocated"], **th
        )
        rows.append({"id": id_, "name": name, "kind": id_, **_card(card)})
    allowances = {
        a.model: a.amount
        for a in await db.scalars(
            select(DepartmentModelAllowance).where(
                DepartmentModelAllowance.department_id == dept.id
            )
        )
    }
    return _card(d["card"]), rows, _models(allowances, spend, "department", dept.id)


def _grant_totals(rows):
    return {
        "departments": len({r["department_id"] for r in rows}),
        "groups": len({r["group_id"] for r in rows if r["group_id"] is not None}),
        "grants": len(rows),
        **{key: sum((r[key] for r in rows), ZERO) for key in GRANT_VALUES},
    }


async def grants_report(db, principal, settings, clock, department, project_ref):
    rows = await grants.list_grants(
        db,
        principal,
        clock,
        department=department,
        project_ref=project_ref,
        timezone=settings.app_timezone,
    )
    projects = defaultdict(list)
    for row in rows:
        projects[row["project_ref"]].append(row)
    return {
        "rows": [
            {"project_ref": ref, **_grant_totals(projects[ref])}
            for ref in sorted(projects, key=lambda ref: (ref is not None, ref or ""))
        ],
        "totals": _grant_totals(rows),
    }
