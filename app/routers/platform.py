import time

from fastapi import APIRouter, Request, Response
from sqlalchemy import func, select, update

from app import metrics
from app.auth.session import CurrentPrincipal, Db
from app.errors import ApiError, invalid_request
from app.litellm_client import HealthReadiness
from app.models import Employee, LitellmWrite, SpendSnapshot
from app.models import Request as DomainRequest
from app.permissions import ADMIN
from app.schemas.platform import OrganizationCreateIn
from app.services import platform
from app.writes import Write, write_response

router = APIRouter()
HEALTH_TTL_S = 10


@router.post("/organizations")
async def create_organization(body: OrganizationCreateIn, ctx: Write, principal: CurrentPrincipal):
    return await write_response(ctx, await platform.create_organization(ctx, principal, body))


async def _health(request: Request) -> HealthReadiness | None:
    """One cached call serves both ``litellm_reachable`` and the Redis row (§5.4)."""
    cached = getattr(request.app.state, "litellm_health", None)
    if cached and time.monotonic() - cached[0] < HEALTH_TTL_S:
        return cached[1]
    try:
        result = await request.app.state.litellm.health_readiness()
    except Exception:
        result = None
    request.app.state.litellm_health = (time.monotonic(), result)
    return result


def _redis_status(health: HealthReadiness | None) -> str:
    if health is None:
        return "unknown"
    if health.cache is None:
        return "not_configured"
    if health.cache == "redis" and health.status == "healthy":
        return "connected"
    return "unreachable"


@router.get("/status")
async def status(request: Request, principal: CurrentPrincipal, db: Db):
    """§5.4 banner data. No secrets."""
    counts = await metrics.outbox_counts(db)
    oldest = await db.scalar(
        select(func.min(LitellmWrite.created_at)).where(LitellmWrite.status == "pending")
    )
    health = await _health(request)
    pending_placements = None
    if principal.is_admin:
        pending_placements = await db.scalar(
            select(func.count()).select_from(Employee).where(Employee.status == "pending_placement")
        )
    return {
        "litellm_reachable": health is not None,
        "redis": _redis_status(health),
        "litellm_version": health.litellm_version if health else None,
        "pending_placements": pending_placements,
        "outbox": {
            "pending": counts["pending"],
            "failed": counts["failed"],
            "oldest_pending_at": oldest,
        },  # fmt: skip
        "spend_taken_at": await db.scalar(select(func.max(SpendSnapshot.taken_at))),
        "last_sync": None,
    }


def _admin_only(principal) -> None:
    if not principal.is_admin:
        raise ApiError(403, "FORBIDDEN", "Forbidden")


@router.get("/outbox")
async def list_outbox(principal: CurrentPrincipal, db: Db, status: str | None = None):
    _admin_only(principal)
    if status is not None and status not in metrics.STATUSES:
        raise invalid_request("status must be pending, applied or failed")
    q = select(LitellmWrite).order_by(LitellmWrite.id.desc()).limit(200)
    if status:
        q = q.where(LitellmWrite.status == status)
    return [
        {
            "id": w.id,
            "entity": w.entity,
            "entity_id": w.entity_id,
            "endpoint": w.endpoint,
            "status": w.status,
            "attempts": w.attempts,
            "last_error": w.last_error,
            "idempotency_key": w.idempotency_key,
            "created_at": w.created_at,
        }  # fmt: skip
        for w in await db.scalars(q)
    ]


@router.post("/outbox/{write_id}/retry")
async def retry_outbox(write_id: int, principal: CurrentPrincipal, ctx: Write):
    _admin_only(principal)
    row = await ctx.db.get(LitellmWrite, write_id, with_for_update=True)
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Not found")
    ctx.audit("outbox", write_id, {"status": row.status}, {"status": "pending"}, ADMIN)
    row.status = "pending"
    await ctx.db.execute(
        update(DomainRequest)
        .where(DomainRequest.application_write_id == write_id)
        .values(status="pending_apply")
    )
    await ctx.db.commit()
    return {"id": write_id, "status": "pending", "request_id": ctx.request_id}


@router.get("/metrics")
async def prometheus(db: Db):
    return Response(await metrics.render(db), media_type="text/plain; version=0.0.4")
