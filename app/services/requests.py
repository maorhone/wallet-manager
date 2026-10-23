"""Requests center (5.7, D-044/D-068/D-074): one record per request, credits and new initiative."""

from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import func, select

from app.db import LockKind
from app.directory import Directory
from app.errors import ApiError, invalid_request
from app.models import Employee, Grp, Request, RequestEvent
from app.permissions import ADMIN, HEAD, REP, Principal, group_roles_of, target_tier
from app.schemas.initiatives import InitiativeIn
from app.services import initiatives, notifications
from app.services.personal_values import OWNER_ROLE, authorize
from app.services.validation import check_sum, group_allocated
from app.writes import WriteContext

TYPES = ("credits", "initiative", "throughput")
STATUSES = ("submitted", "forwarded", "approved", "rejected", "applied", "pending_apply", "failed")


def _forbidden() -> ApiError:
    return ApiError(403, "FORBIDDEN", "Your role does not permit this action")


def _not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "Request not found")


async def _handler_role(db, principal: Principal, req: Request) -> str | None:
    if req.type == "credits" and req.requester_upn == principal.upn:
        return None
    if principal.is_admin:
        return ADMIN
    if req.handler_role == HEAD and HEAD in await group_roles_of(db, principal, req.scope_id):
        return HEAD
    if req.handler_role == REP:
        group = await db.get(Grp, req.scope_id)
        if (
            group is not None
            and group.department_id == req.handler_scope
            and REP in principal.department_roles(group.department_id)
        ):
            return REP
    return None


def _view(req: Request) -> dict:
    return {
        "id": req.id, "type": req.type, "scope_kind": req.scope_kind, "scope_id": req.scope_id,
        "requester_upn": req.requester_upn, "handler_role": req.handler_role,
        "handler_scope": req.handler_scope, "status": req.status, "amount": req.amount,
        "payload": req.payload, "created_at": req.created_at, "updated_at": req.updated_at,
    }  # fmt: skip


async def create(ctx: WriteContext, principal: Principal, directory: Directory, body) -> list[dict]:
    """An active employee asks their section head; the section is the request's scope."""
    employee = await ctx.db.get(Employee, principal.upn)
    if employee is None or employee.status != "active" or employee.group_id is None:
        raise _forbidden()
    group_id = employee.group_id
    await ctx.lock(LockKind.GROUP, group_id)
    await ctx.db.refresh(employee)
    if employee.status != "active" or employee.group_id != group_id:
        raise ApiError(409, "CONFLICT", "The requester's membership changed")
    need = body.payload.get("need")
    if not isinstance(need, str) or not need.strip() or len(need) > 1000:
        raise invalid_request("A short need description is required", {"field": "payload.need"})
    if body.type == "initiative" and not str(body.payload.get("name", "")).strip():
        raise invalid_request("An initiative request needs a name", {"field": "payload.name"})
    group = await ctx.db.get(Grp, employee.group_id)
    handler_role = (
        OWNER_ROLE[await target_tier(ctx.db, directory, employee.upn)]
        if body.type == "credits"
        else HEAD
    )
    handler_scope = (
        group.id if handler_role == HEAD else (group.department_id if handler_role == REP else None)
    )
    req = Request(
        type=body.type, scope_kind="group", scope_id=employee.group_id,
        requester_upn=principal.upn, handler_role=handler_role, handler_scope=handler_scope,
        amount=body.amount, payload=body.payload,
    )  # fmt: skip
    ctx.db.add(req)
    await ctx.db.flush()
    ctx.db.add(RequestEvent(request_id=req.id, actor_upn=principal.upn, action="submitted"))
    ctx.audit(
        "request", req.id, {}, {"type": req.type, "amount": req.amount}, "employee",
        group_id=group.id, department_id=group.department_id,
    )  # fmt: skip
    recipients = (
        await notifications.heads_of(ctx.db, directory, group.id)
        if handler_role == HEAD
        else await notifications.reps_or_admins(
            ctx.db, directory, group.department_id if handler_role == REP else None
        )
    )
    await notifications.notify(
        ctx.db, recipients - {principal.upn}, "request_submitted",
        "request", req.id, {"request_id": req.id, "type": req.type, "requester": principal.upn},
        f"request:{req.id}:submitted", settings=ctx.settings,
    )  # fmt: skip
    return [{"entity": "request", "id": req.id, "values": {"status": req.status}}]


async def list_requests(
    db, principal: Principal, box: str, type: str | None, status: str | None
) -> dict:
    if box not in ("mine", "to_handle"):
        raise invalid_request("box must be mine or to_handle")
    for value, allowed, name in ((type, TYPES, "type"), (status, STATUSES, "status")):
        if value is not None and value not in allowed:
            raise invalid_request(f"Unknown {name}", {name: value})
    query = select(Request).order_by(Request.id)
    if type:
        query = query.where(Request.type == type)
    if status:
        query = query.where(Request.status == status)
    if box == "mine":
        items = await db.scalars(query.where(Request.requester_upn == principal.upn))
        return {"items": [_view(r) for r in items]}
    items = []
    for req in await db.scalars(query):
        if await _handler_role(db, principal, req):
            items.append(_view(req))
    return {"items": items}


async def get(db, principal: Principal, request_id: int) -> dict:
    req = await db.get(Request, request_id)
    if req is None or (
        req.requester_upn != principal.upn and await _handler_role(db, principal, req) is None
    ):
        raise _not_found()
    events = await db.scalars(
        select(RequestEvent).where(RequestEvent.request_id == req.id).order_by(RequestEvent.id)
    )
    return {
        **_view(req),
        "events": [
            {"actor_upn": e.actor_upn, "action": e.action, "comment": e.comment,
             "created_at": e.created_at}
            for e in events
        ],
    }  # fmt: skip


async def decide(
    ctx: WriteContext, principal: Principal, directory: Directory, request_id: int, body
) -> list[dict]:
    req = await ctx.db.get(Request, request_id)
    if req is None:
        raise _not_found()
    role = await _handler_role(ctx.db, principal, req)
    if role is None:
        raise _forbidden()
    await ctx.lock(LockKind.GROUP, req.scope_id)
    await ctx.db.refresh(req)
    role = await _handler_role(ctx.db, principal, req)
    if role is None:
        raise _forbidden()
    if req.status != "submitted":
        raise ApiError(409, "CONFLICT", "The request was already decided")
    group = await ctx.db.get(Grp, req.scope_id)
    before = {"status": req.status}
    if body.decision == "approve":
        amount = body.amount if body.amount is not None else req.amount
        if amount is None or amount <= 0:
            raise invalid_request("An approved amount is required", {"field": "amount"})
        if req.type == "credits":
            await _apply_credits(ctx, principal, directory, req, group, Decimal(amount))
            req.status = "pending_apply"
        else:
            await _apply_initiative(ctx, principal, req, group, Decimal(amount))
            req.status = "applied"
        req.amount = amount
    else:
        req.status = "rejected"
    ctx.db.add(
        RequestEvent(
            request_id=req.id, actor_upn=principal.upn, action=body.decision, comment=body.comment
        )
    )
    req.updated_at = func.now()
    ctx.audit(
        "request", req.id, before, {"status": req.status, "amount": req.amount}, role,
        group_id=group.id, department_id=group.department_id,
    )  # fmt: skip
    kind = "request_approved" if body.decision == "approve" else "request_rejected"
    await notifications.notify(
        ctx.db, {req.requester_upn}, kind, "request", req.id,
        {"request_id": req.id, "type": req.type, "amount": req.amount},
        f"request:{req.id}:{req.status}", settings=ctx.settings,
    )  # fmt: skip
    return [{"entity": "request", "id": req.id, "values": {"status": req.status}}]


async def _apply_credits(
    ctx, principal, directory, req: Request, group: Grp, amount: Decimal
) -> None:
    """Raise the requester's base cap by ``amount``, within the section's allocatable."""
    employee = await ctx.db.get(Employee, req.requester_upn)
    if employee is None or employee.status != "active" or employee.group_id != group.id:
        raise ApiError(409, "CONFLICT", "The requester is no longer an active member")
    role = authorize(principal, employee, group, await target_tier(ctx.db, directory, employee.upn))
    allocated = await group_allocated(ctx.db, group)
    if err := check_sum(allocated + amount, group.base_budget, group.overalloc_factor):
        raise err
    before = {"base_cap": employee.base_cap}
    employee.base_cap += amount
    ctx.audit(
        "employee", employee.upn, before, {"base_cap": employee.base_cap}, role,
        group_id=group.id, department_id=group.department_id,
    )  # fmt: skip
    req.application_write_id = await ctx.enqueue("employee", employee.upn, "/team/member_update")


async def _apply_initiative(ctx, principal, req: Request, group: Grp, amount: Decimal) -> None:
    p = req.payload
    try:
        body = InitiativeIn(
            name=str(p.get("name", "")), owner_upn=p.get("owner_upn") or req.requester_upn,
            period_start=p.get("period_start"), period_end=p.get("period_end"), budget=amount,
        )  # fmt: skip
    except ValidationError as exc:
        raise invalid_request("Invalid initiative details", {"errors": str(exc)}) from None
    await initiatives.create(ctx, principal, group.id, body)
