"""Org Systems: systems funded from the organization envelope, in a virtual LiteLLM org (5.4)."""

from decimal import Decimal

from sqlalchemy import func, select

from app.db import LockKind
from app.errors import ApiError, invalid_request
from app.models import (
    Department,
    Grp,
    Initiative,
    LitellmWrite,
    Organization,
    ProductionSystem,
    ServiceKey,
)
from app.permissions import Principal, can_manage_org_systems
from app.schemas.org_systems import KeyMoveIn, OrgSystemIn
from app.services.service_keys import FIELDS, owner_lock, require_team_applied, values
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
    # Every team waits for the systems org's creation row, also in later requests.
    org_row = await ctx.enqueue("systems_org", org.id, "/organization/new") if first else None
    org_row = org_row or await ctx.db.scalar(
        select(func.max(LitellmWrite.id)).where(
            LitellmWrite.entity == "systems_org",
            LitellmWrite.entity_id == str(org.id),
            LitellmWrite.endpoint == "/organization/new",
        )
    )
    await ctx.enqueue("production_system", system.id, "/team/new", depends_on=org_row)
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
    # Authority comes from the target organization (checked against the source's below); take
    # the source owner's lock too so a concurrent rotate/revoke cannot interleave.
    seen = (key.owner_kind, key.owner_id)
    await owner_lock(ctx, *seen)
    await ctx.lock(LockKind.PRODUCTION, -target.organization_id)
    await ctx.db.refresh(key)
    if (key.owner_kind, key.owner_id) != seen:
        raise invalid_request("Service key changed owner; retry", {"reason": "owner_changed"})
    await ctx.db.refresh(target)
    if key.status != "active" or not key.litellm_token_hash:
        raise invalid_request("Service key is not active", {"status": key.status})
    if (key.owner_kind, key.owner_id) == ("system", target.id) or target.status != "active":
        raise invalid_request("Key cannot move to this system", {"field": "target_system_id"})
    if key.owner_kind in {"group", "initiative"}:
        # Initiatives and systems have independent IDs. Resolve the initiative's
        # actual section under the source owner lock; an equal system ID grants
        # no authority over its key.
        group_id = key.owner_id
        if key.owner_kind == "initiative":
            initiative = await ctx.db.get(Initiative, key.owner_id)
            if initiative is None:
                raise ApiError(404, "NOT_FOUND", "Owner not found")
            group_id = initiative.group_id
        group = await ctx.db.get(Grp, group_id)
        department = await ctx.db.get(Department, group.department_id) if group else None
        if department is None:
            raise ApiError(404, "NOT_FOUND", "Owner not found")
        source_org = department.organization_id
    elif key.owner_kind == "system":
        source = await ctx.db.get(ProductionSystem, key.owner_id)
        if source is None:
            raise ApiError(404, "NOT_FOUND", "Owner not found")
        source_org = source.organization_id
        if source.funding == "department":
            department = await ctx.db.get(Department, source.department_id)
            if department is None:
                raise ApiError(404, "NOT_FOUND", "Owner not found")
            source_org = department.organization_id
    else:
        raise invalid_request("Unknown key owner", {"field": "owner_kind"})
    if source_org != target.organization_id:
        raise invalid_request("Key belongs to another organization", {"field": "target_system_id"})
    await require_team_applied(ctx.db, "production_system", target.id)
    allocated = await key_allocated(ctx.db, "system", target.id)
    if err := check_sum(allocated + (key.max_budget or Decimal(0)), target.budget):
        raise err
    before = {**values(key), "owner_kind": key.owner_kind, "owner_id": key.owner_id}
    source_commitment = None
    if key.owner_kind == "initiative":
        # Moving the credential cannot release its source grant: its current and
        # retired generations may already have spent (or still be in flight).
        # Keep their reservation under the same section lock, in this transaction.
        tombstone = ServiceKey(
            owner_kind="initiative", owner_id=key.owner_id,
            max_budget=key.max_budget, retired_budget=key.retired_budget,
            status="revoked", created_by=principal.upn,
        )  # fmt: skip
        ctx.db.add(tombstone)
        await ctx.db.flush()
        source_commitment = {
            "id": tombstone.id,
            "initiative_id": tombstone.owner_id,
            "max_budget": tombstone.max_budget,
            "retired_budget": tombstone.retired_budget,
        }
        key.retired_budget = Decimal(0)
    key.owner_kind, key.owner_id = "system", target.id
    after = {**{f: getattr(key, f) for f in FIELDS}, "owner_kind": "system", "owner_id": target.id}
    audit_after = {**after, "source_commitment": source_commitment} if source_commitment else after
    ctx.audit("service_key", key.id, before, audit_after, role, department_id=None)
    await ctx.enqueue("service_key", key.id, "/key/update")
    return [{"entity": "service_key", "id": key.id, "values": after}]
