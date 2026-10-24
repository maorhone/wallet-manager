"""Per-model service status from LiteLLM ``/health`` polling (SoT D-060, MAOR-DECISIONS 13).

A row older than twice the poll interval, or no row at all, reads as ``unknown`` and is never
reported as healthy.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.config import Settings
from app.litellm_client import LiteLLMError
from app.models import ServiceHealth

OK, LIMITED, LOADED, UNKNOWN = "ok", "limited", "loaded", "unknown"


def classify(healthy: bool, error: str | None) -> str:
    if healthy:
        return OK
    text = (error or "").lower()
    if "ratelimit" in text.replace(" ", "") or "429" in text or "quota" in text:
        return LIMITED
    if any(w in text for w in ("overload", "timeout", "timed out", "503", "busy", "capacity")):
        return LOADED
    return UNKNOWN


async def run_service_health(sf, client, clock, *, settings: Settings) -> int:
    """Poll once and upsert one row per model. A failed poll keeps old rows, which then go stale."""
    try:
        probes = await client.health_models()
    except LiteLLMError:
        return 0
    now = clock.now()
    async with sf() as db:
        for entry, healthy in probes:
            state = classify(healthy, entry.error)
            detail = None if healthy else (entry.error or "")[:500]
            row = dict(model=entry.model, state=state, detail=detail, checked_at=now)
            stmt = insert(ServiceHealth).values(row)
            await db.execute(stmt.on_conflict_do_update(index_elements=["model"], set_=row))
        await db.commit()
    return len(probes)


async def current(db, now: datetime, settings: Settings, *, detail: bool) -> dict:
    stale_after = timedelta(seconds=2 * settings.service_health_interval_s)
    models = []
    for r in await db.scalars(select(ServiceHealth).order_by(ServiceHealth.model)):
        stale = now - r.checked_at > stale_after
        item = {
            "model": r.model,
            "state": UNKNOWN if stale else r.state,
            "checked_at": r.checked_at,
        }
        if detail:
            item["detail"] = r.detail
        models.append(item)
    states = {m["state"] for m in models}
    overall = (
        UNKNOWN if not models else next((s for s in (UNKNOWN, LOADED, LIMITED) if s in states), OK)
    )
    return {"state": overall, "models": models, "taken_at": now}
