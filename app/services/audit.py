"""FR-A1 audit timeline with the resulting LiteLLM calls (§12.2 history row)."""

from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError, invalid_request
from app.models import AuditLog, Employee, LitellmWrite
from app.permissions import Principal, can_view_department, can_view_members

LIMIT = 500


async def authorize(db: AsyncSession, principal: Principal, entity: str | None, id: str | None):
    """Admin: any filter. Others must name a visible employee, group or department."""
    if principal.is_admin:
        return
    if not entity or not id:
        raise invalid_request("entity and id are required")
    if entity == "employee":
        if id == principal.upn:
            return
        emp = await db.get(Employee, id)
        if emp is None or emp.group_id is None:
            raise ApiError(404, "NOT_FOUND", "Not found")
        await can_view_members(db, principal, emp.group_id)
    elif entity == "group" and id.isdigit():
        await can_view_members(db, principal, int(id))
    elif entity == "department" and id.isdigit():
        await can_view_department(db, principal, int(id))
    else:
        raise invalid_request("entity must be employee, group or department")


async def timeline(
    db: AsyncSession,
    entity: str | None,
    id: str | None,
    actor: str | None,
    start: datetime | None,
    end: datetime | None,
) -> list[dict]:
    q = select(AuditLog).order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(LIMIT)
    if entity and id:
        match = and_(AuditLog.entity == entity, AuditLog.entity_id == id)
        if entity == "group":  # member changes belong to the group's timeline
            match = or_(
                match,
                and_(AuditLog.entity == "employee", AuditLog.after["group_id"].astext == id),
            )
        q = q.where(match)
    elif entity:
        q = q.where(AuditLog.entity == entity)
    if actor:
        q = q.where(AuditLog.actor_upn == actor)
    if start:
        q = q.where(AuditLog.at >= start)
    if end:
        q = q.where(AuditLog.at <= end)
    rows = list(await db.scalars(q))

    request_ids = {r.request_id for r in rows if r.request_id}
    calls: dict[tuple, list[dict]] = {}
    if request_ids:
        rid = func.split_part(LitellmWrite.idempotency_key, ":", 1)
        writes = await db.execute(
            select(rid, LitellmWrite).where(rid.in_(request_ids)).order_by(LitellmWrite.id)
        )
        for key, w in writes.all():
            calls.setdefault((key, w.entity, w.entity_id), []).append(
                {
                    "endpoint": w.endpoint,
                    "status": w.status,
                    "attempts": w.attempts,
                    "last_error": w.last_error,
                }  # fmt: skip
            )
    return [
        {
            "id": r.id,
            "at": r.at,
            "actor_upn": r.actor_upn,
            "actor_role": r.actor_role,
            "entity": r.entity,
            "entity_id": r.entity_id,
            "before": r.before,
            "after": r.after,
            "reason": r.reason,
            "request_id": r.request_id,
            "litellm_calls": calls.get((r.request_id, r.entity, r.entity_id), []),
        }  # fmt: skip
        for r in rows
    ]
