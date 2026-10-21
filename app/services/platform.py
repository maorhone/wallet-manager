"""§11.1 Platform: policies, department/group creation, role bindings. Admin only throughout."""

import math
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.db import LockKind
from app.directory import Directory
from app.errors import ApiError, invalid_request
from app.models import Department, Grp, Policy, RoleBinding
from app.permissions import ADMIN, ROLE_BINDING_SCOPES, Principal, can_manage_platform
from app.schemas.platform import DepartmentCreateIn, GroupCreateIn, RoleBindingIn
from app.services.owners import holders
from app.writes import WriteContext

# §11.1: the policy keys ``GET /policies`` returns today, plus ``overalloc_factors``
_RANGE_KEYS = ("concurrency", "rpm")
_PLAIN_KEYS = ("overalloc_factor_default", "thresholds")
_ALLOWED_KEYS = frozenset({*_RANGE_KEYS, *_PLAIN_KEYS, "overalloc_factors"})


def _policy_number(value) -> bool:
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _creation_conflict(exc: IntegrityError) -> None:
    # asyncpg exposes constraint_name on the wrapped PostgreSQL exception.
    constraint = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
    fields = {
        "department_litellm_org_id_key": "litellm_org_id",
        "grp_litellm_team_id_key": "litellm_team_id",
        "uq_department_ad_group_dn": "ad_group_dn",
        "uq_grp_ad_group_dn": "ad_group_dn",
        "uq_grp_department_staff": "kind",
    }
    if constraint in fields:
        field = fields[constraint]
        message = (
            "Department already has a staff group (D13)"
            if field == "kind"
            else f"{field} already exists"
        )
        raise invalid_request(message, {"field": field}) from exc
    raise exc


async def set_policies(ctx: WriteContext, principal: Principal, body: dict) -> list[dict]:
    role = can_manage_platform(principal)
    for key in body:
        if key not in _ALLOWED_KEYS or key.startswith("member_defaults."):
            raise invalid_request(f"Unknown policy key: {key}", {"key": key})
    for key in _RANGE_KEYS:
        if key in body:
            v = body[key]
            if (
                not isinstance(v, dict)
                or set(v) != {"min", "default", "max"}
                or any(type(n) is not int for n in v.values())
            ):
                raise invalid_request(
                    f"{key}: expected integer min, default and max", {"field": key}
                )
            if not v["min"] <= v["default"] <= v["max"]:
                raise invalid_request(f"{key}: min must be <= default <= max", {"field": key})
    if "thresholds" in body:
        v = body["thresholds"]
        if (
            not isinstance(v, dict)
            or not set(v) <= {"near", "full"}
            or any(not _policy_number(n) for n in v.values())
        ):
            raise invalid_request(
                "thresholds must map near/full to numbers", {"field": "thresholds"}
            )
    if "overalloc_factor_default" in body:
        v = body["overalloc_factor_default"]
        if not _policy_number(v) or not 1 <= v <= 2:
            raise invalid_request(
                "overalloc_factor_default must be within [1.0, 2.0]",
                {"field": "overalloc_factor_default"},
            )

    overalloc_factors = body.get("overalloc_factors", {})
    if not isinstance(overalloc_factors, dict):
        raise invalid_request(
            "overalloc_factors must be a group-id map", {"field": "overalloc_factors"}
        )
    for k, factor in overalloc_factors.items():
        if (
            not isinstance(k, str)
            or not k.isascii()
            or not k.isdigit()
            or len(k) > 19
            or not 0 < int(k) <= 9223372036854775807
        ):
            raise invalid_request("Invalid group id", {"field": "overalloc_factors"})
        if not _policy_number(factor) or not 1 <= factor <= 2:
            raise invalid_request(
                "overalloc_factors value must be within [1.0, 2.0]",
                {"field": "overalloc_factors"},
            )
    groups: dict[int, Grp] = {}
    if overalloc_factors:
        ids = [int(k) for k in overalloc_factors]
        groups = {g.id: g for g in await ctx.db.scalars(select(Grp).where(Grp.id.in_(ids)))}
        for k in overalloc_factors:
            gid = int(k)
            if gid not in groups:
                raise invalid_request("Unknown group id", {"group_id": gid})

    entities = []
    for key, value in body.items():
        if key == "overalloc_factors":
            continue
        row = await ctx.db.get(Policy, key)
        before = row.value if row else None
        if row is None:
            ctx.db.add(Policy(key=key, value=value))
        else:
            row.value = value
        ctx.audit("policy", key, {key: before}, {key: value}, role)
        entities.append({"entity": "policy", "id": key, "values": {key: value}})

    for k, factor in overalloc_factors.items():
        gid = int(k)
        group = groups[gid]
        before = {"overalloc_factor": group.overalloc_factor}
        group.overalloc_factor = Decimal(str(factor))
        after = {"overalloc_factor": group.overalloc_factor}
        ctx.audit(
            "group", gid, before, after, role, group_id=gid, department_id=group.department_id
        )
        entities.append({"entity": "group", "id": gid, "values": after})

    return entities


async def create_department(
    ctx: WriteContext, principal: Principal, body: DepartmentCreateIn
) -> list[dict]:
    role = can_manage_platform(principal)
    await ctx.lock(LockKind.PLATFORM, 0)
    if body.litellm_org_id.endswith("-prod"):
        # DR4: the "-prod" suffix is reserved for generated production org ids (FR-K1),
        # so a department can never be created with an id a future production org will claim.
        raise invalid_request(
            "litellm_org_id cannot use the reserved '-prod' suffix", {"field": "litellm_org_id"}
        )
    if await ctx.db.scalar(
        select(Department.id).where(Department.litellm_org_id == body.litellm_org_id)
    ):
        raise invalid_request("litellm_org_id already exists", {"field": "litellm_org_id"})
    if body.ad_group_dn and await ctx.db.scalar(
        select(Department.id).where(Department.ad_group_dn == body.ad_group_dn)
    ):
        raise invalid_request("ad_group_dn already exists", {"field": "ad_group_dn"})

    dept = Department(
        name=body.name, litellm_org_id=body.litellm_org_id, ad_group_dn=body.ad_group_dn,
        budget=body.budget, budget_period=body.budget_period,
    )  # fmt: skip
    ctx.db.add(dept)
    try:
        await ctx.db.flush()
    except IntegrityError as exc:
        _creation_conflict(exc)
    after = {
        "name": dept.name, "litellm_org_id": dept.litellm_org_id, "ad_group_dn": dept.ad_group_dn,
        "budget": dept.budget, "budget_period": dept.budget_period,
    }  # fmt: skip
    ctx.audit("department", dept.id, {}, after, role)
    await ctx.enqueue("department", dept.id, "/organization/new")
    return [{"entity": "department", "id": dept.id, "values": after}]


async def create_group(
    ctx: WriteContext, principal: Principal, department_id: int, body: GroupCreateIn
) -> list[dict]:
    role = can_manage_platform(principal)
    await ctx.lock(LockKind.PLATFORM, 1)
    await ctx.lock(LockKind.DEPARTMENT, department_id)
    if await ctx.db.get(Department, department_id) is None:
        raise ApiError(404, "NOT_FOUND", "Department not found")
    if body.litellm_team_id.startswith("sys-"):
        # DR4: the "sys-" prefix is reserved for generated production system ids (FR-K2),
        # so a group can never be created with an id a future production system will claim.
        raise invalid_request(
            "litellm_team_id cannot use the reserved 'sys-' prefix", {"field": "litellm_team_id"}
        )
    if await ctx.db.scalar(select(Grp.id).where(Grp.litellm_team_id == body.litellm_team_id)):
        raise invalid_request("litellm_team_id already exists", {"field": "litellm_team_id"})
    if body.ad_group_dn and await ctx.db.scalar(
        select(Grp.id).where(Grp.ad_group_dn == body.ad_group_dn)
    ):
        raise invalid_request("ad_group_dn already exists", {"field": "ad_group_dn"})
    if body.kind == "department_staff" and await ctx.db.scalar(
        select(Grp.id).where(Grp.department_id == department_id, Grp.kind == "department_staff")
    ):
        raise invalid_request("Department already has a staff group (D13)", {"field": "kind"})

    group = Grp(
        litellm_team_id=body.litellm_team_id, department_id=department_id, name=body.name,
        ad_group_dn=body.ad_group_dn, kind=body.kind, base_budget=Decimal(0),
        default_member_cap=body.default_member_cap, status="active",
    )  # fmt: skip
    ctx.db.add(group)
    try:
        await ctx.db.flush()
    except IntegrityError as exc:
        _creation_conflict(exc)
    after = {
        "name": group.name, "litellm_team_id": group.litellm_team_id,
        "ad_group_dn": group.ad_group_dn, "kind": group.kind,
        "default_member_cap": group.default_member_cap,
    }  # fmt: skip
    ctx.audit("group", group.id, {}, after, role, department_id=department_id)
    await ctx.enqueue("group", group.id, "/team/new")
    return [{"entity": "group", "id": group.id, "values": after}]


async def list_role_bindings(
    db, directory: Directory, scope_kind: str, scope_id: int | None
) -> list[dict]:
    rows = await db.scalars(
        select(RoleBinding).where(
            RoleBinding.scope_kind == scope_kind,
            RoleBinding.scope_id.is_(None)
            if scope_id is None
            else RoleBinding.scope_id == scope_id,
        )  # fmt: skip
    )
    by_role: dict[str, list[str]] = {}
    for r in rows:
        by_role.setdefault(r.role, []).append(r.ad_group_dn)
    return [
        {
            "scope_kind": scope_kind,
            "scope_id": scope_id,
            "role": role,
            "ad_group_dns": sorted(dns),
            "members": await holders(db, directory, role, scope_kind, scope_id),
        }  # fmt: skip
        for role, dns in sorted(by_role.items())
    ]


async def put_role_bindings(
    ctx: WriteContext, principal: Principal, body: RoleBindingIn
) -> list[dict]:
    role = can_manage_platform(principal)
    allowed = ROLE_BINDING_SCOPES[body.scope_kind]
    if body.role not in allowed:
        raise invalid_request(
            f"{body.role} is not allowed for scope {body.scope_kind}", {"field": "role"}
        )
    if body.scope_kind == "global":
        if body.scope_id is not None:
            raise invalid_request("A global scope has no scope_id", {"field": "scope_id"})
    elif body.scope_id is None:
        raise invalid_request("scope_id is required for this scope_kind", {"field": "scope_id"})
    elif body.scope_kind == "department":
        if await ctx.db.get(Department, body.scope_id) is None:
            raise ApiError(404, "NOT_FOUND", "Department not found")
    elif await ctx.db.get(Grp, body.scope_id) is None:
        raise ApiError(404, "NOT_FOUND", "Group not found")

    new_dns = set(body.ad_group_dns)
    if body.role == ADMIN and body.scope_kind == "global" and not new_dns:
        raise invalid_request("Cannot remove the last global admin binding")

    existing = list(
        await ctx.db.scalars(
            select(RoleBinding).where(
                RoleBinding.role == body.role,
                RoleBinding.scope_kind == body.scope_kind,
                RoleBinding.scope_id.is_(None)
                if body.scope_id is None
                else RoleBinding.scope_id == body.scope_id,
            )
        )
    )
    existing_dns = {r.ad_group_dn for r in existing}
    removed_dns = existing_dns - new_dns
    if removed_dns:
        await ctx.db.execute(
            delete(RoleBinding).where(
                RoleBinding.role == body.role,
                RoleBinding.scope_kind == body.scope_kind,
                RoleBinding.scope_id.is_(None)
                if body.scope_id is None
                else RoleBinding.scope_id == body.scope_id,
                RoleBinding.ad_group_dn.in_(removed_dns),
            )
        )
    for dn in new_dns - existing_dns:
        ctx.db.add(
            RoleBinding(
                ad_group_dn=dn, role=body.role, scope_kind=body.scope_kind, scope_id=body.scope_id
            )
        )

    entity_id = f"{body.scope_kind}:{body.scope_id}:{body.role}"
    ctx.audit(
        "role_binding", entity_id, {"ad_group_dns": sorted(existing_dns)},
        {"ad_group_dns": sorted(new_dns)}, role,
    )  # fmt: skip
    return [
        {"entity": "role_binding", "id": entity_id, "values": {"ad_group_dns": sorted(new_dns)}}
    ]
