"""Scoped allocation, grants, daily usage, overlap peaks and base/model exhaustion."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.errors import ApiError, invalid_request
from app.models import (
    Department,
    DepartmentModelAllowance,
    Employee,
    EmployeeModelCap,
    Grp,
    SpendSnapshot,
)
from app.permissions import (
    ADMIN,
    HEAD,
    REP,
    VIEWER,
    can_view_department,
    can_view_group,
    can_view_members,
)
from app.services import blocks, grants, views, wallet
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


def report_window(start: date, end: date, timezone: str, limit: int):
    """Inclusive local dates become a UTC half-open window (also across DST)."""
    if end < start or (end - start).days >= limit or end == date.max:
        raise invalid_request(f"range must contain 1 to {limit} days")
    tz = ZoneInfo(timezone)
    return (
        datetime.combine(start, time.min, tz).astimezone(UTC),
        datetime.combine(end + timedelta(days=1), time.min, tz).astimezone(UTC),
    )


@dataclass
class ReportScope:
    kind: str
    entity: Department | Grp | Employee | None
    groups: list[Grp]
    members: list[Employee]
    member_access: bool


async def _report_scope(db, principal, kind, id_, *, need_members=False):
    """Scope authorization is shared by all three reports; D11 precedes member reads."""
    entity = None
    if kind == "global":
        if not (principal.is_admin or principal.has(VIEWER, "global")):
            raise ApiError(403, "FORBIDDEN", "Global reports require admin or global viewer")
        member_access = principal.is_admin
        group_query = select(Grp)
    elif kind == "employee":
        if not id_:
            raise invalid_request("id is required for employee scope")
        entity = await db.get(Employee, id_)
        if entity is None or entity.group_id is None or entity.status == "left":
            raise ApiError(404, "NOT_FOUND", "Employee wallet not found")
        if entity.upn != principal.upn:
            await can_view_members(db, principal, entity.group_id)
        member_access = True
        group_query = select(Grp).where(Grp.id == entity.group_id)
    else:
        try:
            numeric_id = int(id_ or "")
            if not 0 < numeric_id < 2**63:
                raise ValueError
        except ValueError:
            raise invalid_request("id must be a positive integer for this scope") from None
        if kind == "department":
            await can_view_department(db, principal, numeric_id)
            roles = principal.department_roles(numeric_id)
            entity = await db.get(Department, numeric_id)
            group_query = select(Grp).where(Grp.department_id == numeric_id)
        else:
            await can_view_group(db, principal, numeric_id)
            entity = await db.get(Grp, numeric_id)
            roles = principal.group_roles(entity.id, entity.department_id)
            group_query = select(Grp).where(Grp.id == numeric_id)
        if not roles & {ADMIN, REP, HEAD, VIEWER}:
            raise ApiError(403, "FORBIDDEN", "PMO reports are limited to grants")
        member_access = bool(roles & {ADMIN, REP, HEAD})
    if need_members and not member_access:
        raise ApiError(403, "FORBIDDEN", "Member reports require member visibility")
    groups = list(await db.scalars(group_query))
    members = []
    if member_access:
        members = (
            [entity]
            if kind == "employee"
            else list(
                await db.scalars(
                    select(Employee)
                    .where(Employee.group_id.in_([g.id for g in groups]), Employee.status != "left")
                    .order_by(Employee.upn)
                )
            )
        )
    return ReportScope(kind, entity, groups, members, member_access)


def _snapshot_scope(scope, by):
    if by == "member" or scope.kind == "employee":
        return "employee", [m.upn for m in scope.members]
    if scope.kind == "global":
        return "department", list({str(g.department_id) for g in scope.groups})
    return scope.kind, [str(scope.entity.id)]


async def _day_rows(db, kind, ids, start, end):
    return list(
        await db.scalars(
            select(SpendSnapshot).where(
                SpendSnapshot.scope_kind == kind,
                SpendSnapshot.scope_id.in_(ids),
                SpendSnapshot.period >= start.isoformat(),
                SpendSnapshot.period <= end.isoformat(),
                # Month rows must never be added to the daily rows.
                SpendSnapshot.period.like("____-__-__"),
            )
        )
    )


async def _usage_cap(db, scope, settings, clock):
    if scope.kind == "global":
        return sum((d.budget for d in await db.scalars(select(Department))), ZERO)
    if scope.kind == "department":
        return scope.entity.budget
    if scope.kind == "employee":
        shares = await views._shares(db, [scope.entity.group_id])
        return scope.entity.base_cap + sum((s for _, u, s in shares if u == scope.entity.upn), ZERO)
    period = clock.now().astimezone(ZoneInfo(settings.app_timezone)).strftime("%Y-%m")
    amounts = await views._grants_by_group(db, [scope.entity.id], settings.app_timezone, period)
    return scope.entity.base_budget + amounts.get(scope.entity.id, ZERO)


UNTAGGED = "ללא תיוג"


def _bucket(day, granularity):
    if granularity == "week":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1) if granularity == "month" else day


async def usage(
    db, principal, settings, clock, kind, id_, start, end, granularity, by, reader=None
):
    lo, hi = report_window(start, end, settings.app_timezone, 400)
    # Project tags reveal what individuals work on, so they follow the member-visibility rules.
    scope = await _report_scope(db, principal, kind, id_, need_members=by != "model")
    if by == "tag":
        rows = await reader.spend_by_tag(
            lo,
            hi,
            team_ids=None if kind == "global" else [g.litellm_team_id for g in scope.groups],
            users=[scope.entity.upn] if kind == "employee" else None,
            timezone=settings.app_timezone,
        )
        series = defaultdict(Decimal)
        for project, day, spend in rows:
            series[(_bucket(day, granularity), project or UNTAGGED)] += spend
        return {
            "series": [dict(bucket=b, key=k, spend=s) for (b, k), s in sorted(series.items())],
            "cap": await _usage_cap(db, scope, settings, clock),
            "taken_at": clock.now(),
        }
    snapshot_kind, ids = _snapshot_scope(scope, by)
    rows = await _day_rows(db, snapshot_kind, ids, start, end)
    series = defaultdict(Decimal)
    for r in rows:
        if (by == "model" and r.model is None) or (by == "member" and r.model is not None):
            continue
        key = r.model if by == "model" else r.scope_id
        series[(_bucket(date.fromisoformat(r.period), granularity), key)] += r.spend
    return {
        "series": [dict(bucket=b, key=k, spend=s) for (b, k), s in sorted(series.items())],
        "cap": await _usage_cap(db, scope, settings, clock),
        "taken_at": max((r.taken_at for r in rows), default=None),
    }


async def concurrency_peaks(db, principal, settings, reader, kind, id_, start, end):
    lo, hi = report_window(start, end, settings.app_timezone, 31)
    scope = await _report_scope(db, principal, kind, id_)
    rows = await reader.concurrency_peaks(
        lo,
        hi,
        team_ids=None if kind == "global" else [g.litellm_team_id for g in scope.groups],
        users=[scope.entity.upn] if kind == "employee" else None,
        timezone=settings.app_timezone,
    )
    by_user = {}
    for row in rows:
        if row.user is not None and (
            row.user not in by_user
            or row.peak > by_user[row.user].peak
            or (row.peak == by_user[row.user].peak and row.at < by_user[row.user].at)
        ):
            by_user[row.user] = row
    members = [
        dict(
            upn=m.upn,
            display_name=m.display_name,
            peak=by_user[m.upn].peak,
            at=by_user[m.upn].at,
            max_parallel=m.max_parallel,
        )
        for m in scope.members
        if m.upn in by_user
    ]
    members.sort(key=lambda m: (-m["peak"], m["upn"]))
    return {
        "daily": [dict(day=r.day, peak=r.peak, at=r.at) for r in rows if r.user is None],
        "members": members[:100],
    }


async def blocked(db, principal, settings, clock, kind, id_, start, end):
    report_window(start, end, settings.app_timezone, 400)
    scope = await _report_scope(db, principal, kind, id_, need_members=True)
    member_ids = [m.upn for m in scope.members]
    snapshots = await _day_rows(db, "employee", member_ids, start.replace(day=1), end)
    daily = {(r.scope_id, r.period, r.model): r.spend for r in snapshots}
    caps = defaultdict(dict)
    for c in await db.scalars(select(EmployeeModelCap).where(EmployeeModelCap.upn.in_(member_ids))):
        caps[c.upn][c.model] = c.amount
    rows = []
    last_day = min(end, clock.now().astimezone(ZoneInfo(settings.app_timezone)).date())
    for member in scope.members:
        limits = {None: member.base_cap, **caps[member.upn]}
        running = defaultdict(Decimal)
        day = start.replace(day=1)
        while day <= last_day:
            if day.day == 1:
                running.clear()
            for model, cap in limits.items():
                day_spend = daily.get((member.upn, day.isoformat(), model), ZERO)
                running[model] += day_spend
                if day >= start and running[model] >= cap:
                    rows.append(
                        dict(
                            day=day,
                            upn=member.upn,
                            display_name=member.display_name,
                            group_id=member.group_id,
                            kind="base" if model is None else "model",
                            model=model,
                            cap=cap,
                            period_spend_to_date=running[model],
                            day_spend=day_spend,
                        )
                    )
            day += timedelta(days=1)
    rows.sort(key=lambda r: (r["day"], r["upn"], r["kind"], r["model"] or ""))
    return {"rows": rows}


async def block_reasons(db, principal, settings, reader, kind, id_, start, end):
    lo, hi = report_window(start, end, settings.app_timezone, 400)
    scope = await _report_scope(db, principal, kind, id_, need_members=True)
    rows = await reader.failed_requests(
        lo,
        hi,
        team_ids=None if kind == "global" else [g.litellm_team_id for g in scope.groups],
        users=[scope.entity.upn] if kind == "employee" else None,
    )
    by_reason, by_consumer = blocks.aggregate(rows)
    return {"by_reason": by_reason, "by_consumer": by_consumer}
