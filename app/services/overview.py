"""Section budget overview and Action Center (5.17, WF-01): read-only, built on group_summary."""

from decimal import Decimal

from sqlalchemy import select

from app.config import Settings
from app.models import Grp, Initiative, Policy, Request
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
    for child in await db.scalars(
        select(Grp).where(Grp.parent_group_id == group_id, Grp.status != "archived")
    ):
        used = spend.used("group", child.id)
        rows.append(
            {"kind": "group", "id": str(child.id), "name": child.name, "total": child.base_budget,
             "used": used, "percent": _pct(used, child.base_budget), "state": None}
        )  # fmt: skip
    for ini in await db.scalars(
        select(Initiative).where(Initiative.group_id == group_id, Initiative.status == "active")
    ):
        used = spend.used("initiative", ini.id)
        rows.append(
            {"kind": "initiative", "id": str(ini.id), "name": ini.name, "total": ini.budget,
             "used": used, "percent": _pct(used, ini.budget), "state": None}
        )  # fmt: skip
    return {
        "period": s["period"], "reset_date": s["reset_date"], "spend_taken_at": s["spend_taken_at"],
        "group": s["group"],
        "kpis": {
            "total": w["total"], "allocated": w["allocated"], "used": w["used"],
            "remaining": w["remaining"],
            "metered_value": await metered_value(db, "group", group_id, s["period"]),
        },
        "bar": {"used": w["used"], "allocated_unused": max(w["allocated"] - w["used"], 0),
                "unallocated": w["unallocated"], "total": w["total"]},
        "rows": rows,
        "actions": s["actions"],
    }  # fmt: skip


def _pct(used: Decimal, total: Decimal) -> float:
    return float(used / total) if total else 0.0


async def attention(db, principal, settings: Settings, scope: str, raw_period: str | None) -> dict:
    group_id = _group_id(scope)
    o = await overview(db, principal, settings, scope, raw_period)
    row = await db.get(Policy, "shortage_threshold")
    threshold = Decimal(str(row.value)) if row and row.value else DEFAULT_SHORTAGE
    pending = list(
        await db.scalars(
            select(Request).where(
                Request.scope_kind == "group",
                Request.scope_id == group_id,
                Request.status.in_(("submitted", "forwarded")),
            )
        )
    )
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
