"""Service keys: synchronous secret delivery, transactional audit and fixed deletion targets."""

import json
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db import LockKind
from app.errors import ApiError, invalid_request
from app.litellm_client import LiteLLMError, ServiceAccountKeyGenerate
from app.models import Grp, LitellmWrite, Policy, ProductionSystem, ServiceKey
from app.permissions import can_manage_service_keys, can_view_service_keys
from app.services.spend import Spend
from app.services.validation import check_limit, check_sum, group_allocated, key_allocated
from app.writes import sync_status, write_response

GEN = "/key/service-account/generate"
FIELDS = ("alias", "max_budget", "max_parallel", "rpm_limit", "models", "status")


def values(key):
    return {field: getattr(key, field) for field in FIELDS}


async def owner_lock(ctx, kind, owner_id):
    model = Grp if kind == "group" else ProductionSystem
    owner = await ctx.db.get(model, owner_id)
    if owner is None:
        raise ApiError(404, "NOT_FOUND", "Owner not found")
    await ctx.lock(
        LockKind.GROUP if kind == "group" else LockKind.PRODUCTION,
        owner_id if kind == "group" else owner.department_id,
    )
    await ctx.db.refresh(owner)
    return owner


async def generate(ctx, client, owner, key, role, before, *, rotating=False):
    old_hash = key.litellm_token_hash
    key.status = "pending"
    request = ServiceAccountKeyGenerate(
        team_id=owner.litellm_team_id,
        key_alias=f"wm-sk-{key.id}",
        max_budget=float(key.max_budget),
        max_parallel_requests=key.max_parallel,
        rpm_limit=key.rpm_limit,
        models=key.models,
    )
    ctx.audit(
        "service_key",
        key.id,
        before,
        {**values(key), "status": "active"},
        role,
        group_id=owner.id if key.owner_kind == "group" else None,
        department_id=owner.department_id,
    )
    write_id = await ctx.enqueue("service_key", key.id, GEN, payload=request.payload())
    # A pending key reserves budget. The worker never dispatches this synchronous endpoint.
    await ctx.db.commit()
    try:
        result = await client.key_service_account_generate(request)
    except LiteLLMError:
        await owner_lock(ctx, key.owner_kind, key.owner_id)
        await ctx.db.refresh(key)
        key.status = "active" if rotating else "failed"
        write = await ctx.db.get(LitellmWrite, write_id)
        # Never persist an upstream error body, which could echo a secret.
        write.status, write.last_error = "failed", "Service key generation failed"
        await ctx.db.commit()
        raise ApiError(503, "LITELLM_UNAVAILABLE", "Service key generation failed") from None
    await owner_lock(ctx, key.owner_kind, key.owner_id)
    await ctx.db.refresh(key)
    key.litellm_token_hash, key.status = result.token, "active"
    write = await ctx.db.get(LitellmWrite, write_id)
    write.status, write.last_error = "applied", None
    if rotating:
        await ctx.enqueue("service_key", key.id, "/key/delete", payload={"keys": [old_hash]})
    response = await write_response(
        ctx, [{"entity": "service_key", "id": key.id, "values": values(key)}]
    )
    response.body = json.dumps({**json.loads(response.body), "secret": result.key}).encode()
    response.headers["content-length"] = str(len(response.body))
    response.headers["cache-control"] = "no-store"
    return response


async def create(ctx, principal, client, body):
    role = await can_manage_service_keys(ctx.db, principal, body.owner_kind, body.owner_id)
    owner = await owner_lock(ctx, body.owner_kind, body.owner_id)
    if body.owner_kind == "group":
        allocated = await group_allocated(ctx.db, owner)
        err = check_sum(allocated + body.max_budget, owner.base_budget, owner.overalloc_factor)
        entity = "group"
    else:
        allocated = await key_allocated(ctx.db, "system", owner.id)
        err = check_sum(allocated + body.max_budget, owner.budget)
        entity = "production_system"
    if err:
        raise err
    for field, policy_name in (("max_parallel", "concurrency"), ("rpm_limit", "rpm")):
        policy = await ctx.db.get(Policy, policy_name)
        if policy and (
            err := check_limit(field, getattr(body, field), policy.value, principal.is_admin)
        ):
            raise err
    # Missing team/new is not proof the owner is provisioned.
    team_status = await ctx.db.scalar(
        select(LitellmWrite.status)
        .where(
            LitellmWrite.entity == entity,
            LitellmWrite.entity_id == str(owner.id),
            LitellmWrite.endpoint == "/team/new",
        )
        .order_by(LitellmWrite.id.desc())
        .limit(1)
    )
    if team_status != "applied":
        raise invalid_request(
            "Owner team is not provisioned", {"sync_status": team_status or "pending"}
        )
    key = ServiceKey(**body.model_dump(), status="pending", created_by=principal.upn)
    ctx.db.add(key)
    await ctx.db.flush()
    return await generate(ctx, client, owner, key, role, {})


async def change(ctx, principal, client, key_id, action):
    key = await ctx.db.get(ServiceKey, key_id)
    if key is None:
        raise ApiError(404, "NOT_FOUND", "Service key not found")
    role = await can_manage_service_keys(ctx.db, principal, key.owner_kind, key.owner_id)
    owner = await owner_lock(ctx, key.owner_kind, key.owner_id)
    await ctx.db.refresh(key)
    if key.status != "active":
        raise invalid_request("Service key is not active", {"status": key.status})
    before = values(key)
    if action == "rotate":
        return await generate(ctx, client, owner, key, role, before, rotating=True)
    key.status = "revoked"
    ctx.audit(
        "service_key",
        key.id,
        before,
        values(key),
        role,
        group_id=owner.id if key.owner_kind == "group" else None,
        department_id=owner.department_id,
    )
    await ctx.enqueue(
        "service_key", key.id, "/key/delete", payload={"keys": [key.litellm_token_hash]}
    )
    return await write_response(
        ctx, [{"entity": "service_key", "id": key.id, "values": values(key)}]
    )


async def list_keys(db, principal, settings, clock, owner_kind, owner_id):
    await can_view_service_keys(db, principal, owner_kind, owner_id)
    manage = principal.is_admin or (
        owner_kind == "group" and principal.has("group_head", "group", owner_id)
    )
    keys = list(
        await db.scalars(
            select(ServiceKey)
            .where(
                ServiceKey.owner_kind == owner_kind,
                ServiceKey.owner_id == owner_id,
            )
            .order_by(ServiceKey.id)
        )
    )
    statuses = await sync_status(db, "service_key", [str(k.id) for k in keys])
    period = clock.now().astimezone(ZoneInfo(settings.app_timezone)).strftime("%Y-%m")
    spend = await Spend.load(db, period)
    return {
        "items": [
            {
                "id": k.id,
                "owner_kind": k.owner_kind,
                "owner_id": k.owner_id,
                **values(k),
                "used": spend.used("service_key", k.id),
                "sync_status": statuses[str(k.id)],
                "created_at": k.created_at,
                "actions": {
                    "rotate": manage and k.status == "active",
                    "revoke": manage and k.status == "active",
                },
            }
            for k in keys
        ],
        "spend_taken_at": spend.taken_at,
    }
