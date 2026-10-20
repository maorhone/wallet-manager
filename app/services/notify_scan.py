"""Threshold scans behind the notification job (SPEC §7.9 FR-N1): wallets, grants, outbox."""

from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import Settings
from app.directory import Directory
from app.models import Employee, Grant, GrantShare, Grp, LitellmWrite
from app.permissions import ADMIN, PMO
from app.services.attribution import displayed_used
from app.services.notifications import heads_of, notify, upns_of
from app.services.owners import holders
from app.services.spend import Spend
from app.services.views import _Group, _thresholds

EXPIRING_DAYS, UNDERUSED = 7, Decimal("0.2")


def _pct(x: Decimal) -> float:
    return round(float(x), 4)


async def _wallets(db, directory: Directory, settings: Settings, period: str, out: Counter):
    """employee_* and group_* for the current period (80% and 100%)."""
    th, spend = await _thresholds(db), await Spend.load(db, period)
    levels = (("80", th["near"]), ("100", th["full"]))
    for group in await db.scalars(select(Grp).where(Grp.status != "archived").order_by(Grp.id)):
        g = await _Group.load(db, group, spend, None, th, settings.app_timezone)
        heads = await heads_of(db, directory, group.id)
        for level, limit in levels:
            if g.card["percent"] >= limit:
                out[f"group_{level}"] += await notify(
                    db, heads, f"group_{level}", "group", group.id,
                    {"group_id": group.id, "name": group.name, "period": period,
                     "percent": _pct(g.card["percent"]), "used": g.card["used"],
                     "total": g.card["total"]},
                    f"grp:{group.id}:{period}:{level}", settings=settings,
                )  # fmt: skip
        for r in g.rows(spend, th):
            if r["status"] not in ("active", "blocked"):
                continue
            for level, limit in levels:
                if r["percent"] >= limit:
                    out[f"employee_{level}"] += await notify(
                        db, [r["upn"]], f"employee_{level}", "group", group.id,
                        {"group_id": group.id, "period": period, "percent": _pct(r["percent"]),
                         "used": r["used"], "total": r["total"]},
                        f"emp:{period}:{level}", settings=settings,
                    )  # fmt: skip


async def _grant_used(db, g: Grant, spend: Spend, timezone: str = "Asia/Jerusalem") -> Decimal:
    """Σ used of the grant's shares: stored for closed ones, attributed for active ones."""
    shares = list(await db.scalars(select(GrantShare).where(GrantShare.grant_id == g.id)))
    total = sum((s.used for s in shares if s.status == "closed"), Decimal(0))
    ids = {s.id for s in shares if s.status == "active"}
    for upn in {s.upn for s in shares if s.status == "active"}:
        e = await db.get(Employee, upn)
        shown = await displayed_used(
            db, upn, e.base_cap, spend.used("employee", upn), spend.period, timezone
        )
        total += sum((v for sid, v in shown.items() if sid in ids), Decimal(0))
    return total


async def _grants(
    db, directory: Directory, settings: Settings, now: datetime, period: str, out: Counter
):
    spend = await Spend.load(db, period)
    soon = now + timedelta(days=EXPIRING_DAYS)
    grants = await db.scalars(
        select(Grant).where(
            Grant.status == "active", Grant.group_id.is_not(None), Grant.expires_at <= soon
        ).order_by(Grant.id)
    )  # fmt: skip
    for g in grants:
        group = await db.get(Grp, g.group_id)
        to = await heads_of(db, directory, group.id)
        to |= upns_of(await holders(db, directory, PMO, "department", group.department_id))
        base = {"grant_id": g.id, "group_id": g.group_id, "project_ref": g.project_ref,
                "expires_at": g.expires_at.isoformat()}  # fmt: skip
        out["grant_expiring"] += await notify(
            db, to, "grant_expiring", "grant", g.id, base, f"grant:{g.id}:expiring",
            settings=settings,
        )  # fmt: skip
        used = await _grant_used(db, g, spend, settings.app_timezone)
        if used < UNDERUSED * g.amount:
            out["grant_underused"] += await notify(
                db, to, "grant_underused", "grant", g.id,
                {**base, "used": used, "amount": g.amount}, f"grant:{g.id}:underused",
                settings=settings,
            )  # fmt: skip


async def _outbox(db, directory: Directory, settings: Settings, out: Counter):
    admins = upns_of(await holders(db, directory, ADMIN, "global", None))
    failed = await db.execute(
        select(LitellmWrite.id, LitellmWrite.entity, LitellmWrite.entity_id,
               LitellmWrite.endpoint, LitellmWrite.last_error)
        .where(LitellmWrite.status == "failed").order_by(LitellmWrite.id)
    )  # fmt: skip
    for wid, entity, entity_id, endpoint, error in failed.all():
        out["outbox_failed"] += await notify(
            db, admins, "outbox_failed", None, None,
            {"write_id": wid, "entity": entity, "entity_id": entity_id, "endpoint": endpoint,
             "error": (error or "")[:200]},
            f"outbox:{wid}", settings=settings,
        )  # fmt: skip


async def scan(db, directory: Directory, settings: Settings, now: datetime) -> Counter:
    out: Counter = Counter()
    period = now.astimezone(ZoneInfo(settings.app_timezone)).strftime("%Y-%m")
    await _wallets(db, directory, settings, period, out)
    await _grants(db, directory, settings, now, period, out)
    await _outbox(db, directory, settings, out)
    return out
