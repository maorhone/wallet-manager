"""Requests center (5.7, D-044/D-068/D-074): one record per request, credits and new initiative."""

from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import func, select

from app.db import LockKind
from app.directory import Directory
from app.errors import ApiError, invalid_request
from app.models import (
    Employee,
    Grp,
    Initiative,
    LitellmWrite,
    ProductionSystem,
    Request,
    RequestEvent,
)
from app.permissions import ADMIN, AI_TEAM, HEAD, REP, Principal, group_roles_of, target_tier
from app.schemas.groups import MemberLimitsIn
from app.schemas.initiatives import InitiativeIn
from app.schemas.production import ProductionSystemPatch
from app.services import initiatives, members, notifications, production
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
    if req.handler_role == AI_TEAM:
        return AI_TEAM if principal.has(AI_TEAM, "global") else None
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
    target = body.target
    if target is not None and body.type == "initiative":
        raise invalid_request("A new initiative has no target", {"field": "target"})
    if target is not None:
        group_id = await _target_group(ctx.db, principal, target)
        await ctx.lock(LockKind.GROUP, group_id)
        body.payload = {**body.payload, "target": target.model_dump()}
    else:
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
    if body.type == "throughput" and any(k in body.payload for k in _LIMIT_FIELDS):
        _limits_of(body.payload)
    group = await ctx.db.get(Grp, group_id)
    handler_role = (
        OWNER_ROLE[await target_tier(ctx.db, directory, principal.upn)]
        if body.type == "credits" and target is None
        else HEAD
    )
    handler_scope = (
        group.id if handler_role == HEAD else (group.department_id if handler_role == REP else None)
    )
    req = Request(
        type=body.type, scope_kind="group", scope_id=group_id,
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


_LIMIT_FIELDS = ("max_parallel", "rpm_limit", "model_tpm_limits")


async def _target_group(db, principal: Principal, target) -> int:
    """The section that handles a request about an initiative / internal system the caller owns."""
    row = await db.get(Initiative if target.kind == "initiative" else ProductionSystem, target.id)
    if row is None or row.owner_upn != principal.upn or row.status != "active":
        raise _forbidden()
    if target.kind == "initiative":
        return row.group_id
    if row.funding != "department" or row.managing_group_id is None:
        raise invalid_request("This system's requests are not routed in WM yet")
    return row.managing_group_id


def _limits_of(payload: dict) -> dict:
    """The requested limits (D-065); omitted fields keep the member's current value."""
    wanted = {k: payload[k] for k in _LIMIT_FIELDS if payload.get(k) is not None}
    try:
        MemberLimitsIn(**wanted)
    except ValidationError as exc:
        raise invalid_request("Invalid limits", {"errors": str(exc)}) from None
    return wanted


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
    if req.status not in ("submitted", "forwarded"):
        raise ApiError(409, "CONFLICT", "The request was already decided")
    allowed = ("forward", "apply", "reject") if req.type == "throughput" else ("approve", "reject")
    if body.decision not in allowed:
        raise invalid_request("This decision does not fit the request type")
    group = await ctx.db.get(Grp, req.scope_id)
    before = {"status": req.status}
    forwarded_to = None
    if req.type == "throughput":
        forwarded_to = await _decide_throughput(ctx, directory, req, group, role, body)
    elif body.decision == "approve":
        amount = body.amount if body.amount is not None else req.amount
        if amount is None or amount <= 0:
            raise invalid_request("An approved amount is required", {"field": "amount"})
        if req.type == "credits" and req.payload.get("target"):
            req.status = await _apply_target_credits(ctx, principal, req, group, Decimal(amount))
        elif req.type == "credits":
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
    if forwarded_to is not None:
        await notifications.notify(
            ctx.db, forwarded_to, "request_forwarded", "request", req.id,
            {"request_id": req.id, "type": req.type, "requester": req.requester_upn},
            f"request:{req.id}:forwarded:{req.handler_role}", settings=ctx.settings,
        )  # fmt: skip
        return [{"entity": "request", "id": req.id, "values": {"status": req.status}}]
    kind = "request_rejected" if req.status == "rejected" else "request_approved"
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


async def _apply_target_credits(ctx, principal, req: Request, group: Grp, amount: Decimal) -> str:
    """D-046..D-048: the decision applies to the targeted initiative / internal system."""
    t = req.payload["target"]
    if t["kind"] == "initiative":
        row = await ctx.db.get(Initiative, t["id"])
        if row is None or row.status != "active" or row.group_id != group.id:
            raise ApiError(409, "CONFLICT", "The target is no longer active in this section")
        allocated = await group_allocated(ctx.db, group)
        if err := check_sum(allocated + amount, group.base_budget, group.overalloc_factor):
            raise err
        before, row.budget = {"budget": row.budget}, row.budget + amount
        ctx.audit(
            "initiative", row.id, before, {"budget": row.budget}, HEAD,
            group_id=group.id, department_id=group.department_id,
        )  # fmt: skip
        return "applied"
    row = await ctx.db.get(ProductionSystem, t["id"])
    if row is None or row.status != "active" or row.managing_group_id != group.id:
        raise ApiError(409, "CONFLICT", "The target is no longer managed by this section")
    await production.update_system_budget(
        ctx, principal, row.id, ProductionSystemPatch(budget=row.budget + amount)
    )
    req.application_write_id = await ctx.db.scalar(
        select(func.max(LitellmWrite.id)).where(
            LitellmWrite.entity == "production_system", LitellmWrite.entity_id == str(row.id)
        )
    )
    return "pending_apply"


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


async def _decide_throughput(ctx, directory, req: Request, group: Grp, role, body):
    """D-065: heads only forward; the AI team (or an admin) applies or rejects.

    Returns the recipients of a forward, or None once the request is closed.
    """
    decision = body.decision
    if decision == "forward":
        if role not in (HEAD, REP, ADMIN):
            raise _forbidden()
        if req.handler_role == HEAD:
            req.handler_role, req.handler_scope = REP, group.department_id
            recipients = await notifications.reps_or_admins(ctx.db, directory, group.department_id)
        elif req.handler_role == REP:
            req.handler_role, req.handler_scope = AI_TEAM, None
            recipients = await notifications.ai_team_or_admins(ctx.db, directory)
        else:
            raise invalid_request("The request is already with the AI team")
        req.status = "forwarded"
        return recipients - {req.requester_upn}
    if role not in (AI_TEAM, ADMIN):
        raise _forbidden()
    if decision == "reject":
        req.status = "rejected"
        return None
    supplied = _limits_of(req.payload) | _limits_of(body.model_dump())
    if req.payload.get("target"):  # initiatives/systems carry no WM-managed limits: the AI team
        req.status = "applied"  # sets them in LiteLLM; the decision is recorded on the request
        return None
    if not supplied:  # D-045: a narrative-only request needs the AI team's technical values
        raise invalid_request("Name the limits to apply", {"field": "max_parallel"})
    employee = await ctx.db.get(Employee, req.requester_upn)
    if employee is None or employee.status != "active" or employee.group_id != group.id:
        raise ApiError(409, "CONFLICT", "The requester is no longer an active member")
    wanted = {
        "max_parallel": employee.max_parallel, "rpm_limit": employee.rpm_limit,
        "model_tpm_limits": employee.model_tpm_limits or {},
    } | supplied  # fmt: skip
    req.application_write_id = await members.apply_limits(
        ctx, role, group, employee, MemberLimitsIn(**wanted)
    )
    req.status = "pending_apply"
    return None
