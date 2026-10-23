"""Org Systems: systems funded from the organization envelope, in a virtual LiteLLM org (5.4)."""

from decimal import Decimal

from sqlalchemy import func, select

from app.db import LockKind
from app.errors import ApiError, invalid_request
from app.models import Department, Grp, Organization, ProductionSystem, ServiceKey
from app.permissions import Principal, can_manage_org_systems, can_manage_service_keys
from app.schemas.org_systems import KeyMoveIn, OrgSystemIn
from app.services.service_keys import FIELDS, require_team_applied, values
from app.services.validation import check_sum, key_allocated
from app.writes import WriteContext


async def create_org_system(
    ctx: WriteContext, principal: Principal, organization_id: int, body: OrgSystemIn
) -> list[dict]:
    role = can_manage_org_systems(principal, organization_id)
    await ctx.lock(LockKind.PRODUCTION, -organization_id)
    org = await ctx.db.get(Organization, organization_id)
    if org is None:
        raise ApiError(404, "NOT_FOUND", "Organization not found")
    await ctx.db.refresh(org)
    if body.responsible_department_id is not None:
        dept = await ctx.db.get(Department, body.responsible_department_id)
        if dept is None or dept.organization_id != organization_id:
            raise invalid_request(
                "Unknown responsible department", {"field": "responsible_department_id"}
            )
    allocated = await ctx.db.scalar(
        select(func.coalesce(func.sum(ProductionSystem.budget), 0)).where(
            ProductionSystem.organization_id == organization_id,
            ProductionSystem.funding == "organization",
            ProductionSystem.status == "active",
        )
    )
    if err := check_sum(allocated + body.budget, org.systems_envelope):
        raise err
    first = org.systems_litellm_org_id is None
    if first:
        org.systems_litellm_org_id = f"orgsys-{org.id}"
    system = ProductionSystem(
        funding="organization", organization_id=organization_id, name=body.name,
        budget=body.budget, owner_upn=body.owner_upn, status="active",
        responsible_department_id=body.responsible_department_id,
    )  # fmt: skip
    ctx.db.add(system)
    await ctx.db.flush()
    system.litellm_team_id = f"sys-{system.id}"
    after = {
        "name": system.name, "litellm_team_id": system.litellm_team_id, "budget": system.budget,
        "organization_id": organization_id, "owner_upn": system.owner_upn,
        "responsible_department_id": system.responsible_department_id,
    }  # fmt: skip
    ctx.audit("production_system", system.id, {}, after, role)
    if first:
        await ctx.enqueue("systems_org", org.id, "/organization/new", chain=True)
    await ctx.enqueue("production_system", system.id, "/team/new", chain=True)
    return [{"entity": "production_system", "id": system.id, "values": after}]


async def move_key(
    ctx: WriteContext, principal: Principal, key_id: int, body: KeyMoveIn
) -> list[dict]:
    """Move an active key to an Org System (history kept; spend follows the key's new team)."""
    key = await ctx.db.get(ServiceKey, key_id)
    target = await ctx.db.get(ProductionSystem, body.target_system_id)
    if key is None or target is None or target.funding != "organization":
        raise ApiError(404, "NOT_FOUND", "Service key or Org System not found")
    role = can_manage_org_systems(principal, target.organization_id)
    await can_manage_service_keys(ctx.db, principal, key.owner_kind, key.owner_id)
    await ctx.lock(LockKind.PRODUCTION, -target.organization_id)
    await ctx.db.refresh(key)
    await ctx.db.refresh(target)
    if key.status != "active" or not key.litellm_token_hash:
        raise invalid_request("Service key is not active", {"status": key.status})
    if (key.owner_kind, key.owner_id) == ("system", target.id) or target.status != "active":
        raise invalid_request("Key cannot move to this system", {"field": "target_system_id"})
    if key.owner_kind == "group":
        group = await ctx.db.get(Grp, key.owner_id)
        source_org = (await ctx.db.get(Department, group.department_id)).organization_id
    else:
        source = await ctx.db.get(ProductionSystem, key.owner_id)
        source_org = source.organization_id
        if source.funding == "department":
            source_org = (await ctx.db.get(Department, source.department_id)).organization_id
    if source_org != target.organization_id:
        raise invalid_request("Key belongs to another organization", {"field": "target_system_id"})
    await require_team_applied(ctx.db, "production_system", target.id)
    allocated = await key_allocated(ctx.db, "system", target.id)
    if err := check_sum(allocated + (key.max_budget or Decimal(0)), target.budget):
        raise err
    before = {**values(key), "owner_kind": key.owner_kind, "owner_id": key.owner_id}
    key.owner_kind, key.owner_id = "system", target.id
    after = {**{f: getattr(key, f) for f in FIELDS}, "owner_kind": "system", "owner_id": target.id}
    ctx.audit("service_key", key.id, before, after, role, department_id=None)
    await ctx.enqueue("service_key", key.id, "/key/update")
    return [{"entity": "service_key", "id": key.id, "values": after}]
