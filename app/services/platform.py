"""§11.1 Platform: policies, department/group creation, role bindings. Admin only throughout."""

from decimal import Decimal

from sqlalchemy import select

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


async def set_policies(ctx: WriteContext, principal: Principal, body: dict) -> list[dict]:
    role = can_manage_platform(principal)
    for key in body:
        if key not in _ALLOWED_KEYS or key.startswith("member_defaults."):
            raise invalid_request(f"Unknown policy key: {key}", {"key": key})
    for key in _RANGE_KEYS:
        if key in body:
            v = body[key]
            if not v["min"] <= v["default"] <= v["max"]:
                raise invalid_request(f"{key}: min must be <= default <= max", {"field": key})
    if "overalloc_factor_default" in body:
        v = body["overalloc_factor_default"]
        if not 1.0 <= v <= 2.0:
            raise invalid_request(
                "overalloc_factor_default must be within [1.0, 2.0]",
                {"field": "overalloc_factor_default"},
            )

    overalloc_factors: dict[str, float] = body.get("overalloc_factors") or {}
    groups: dict[int, Grp] = {}
    if overalloc_factors:
        ids = [int(k) for k in overalloc_factors]
        groups = {g.id: g for g in await ctx.db.scalars(select(Grp).where(Grp.id.in_(ids)))}
        for k, factor in overalloc_factors.items():
            gid = int(k)
            if gid not in groups:
                raise invalid_request("Unknown group id", {"group_id": gid})
            if not 1.0 <= factor <= 2.0:
                raise invalid_request(
                    "overalloc_factors value must be within [1.0, 2.0]",
                    {"field": "overalloc_factors"},
                )

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
    await ctx.db.flush()
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
    if await ctx.db.get(Department, department_id) is None:
        raise ApiError(404, "NOT_FOUND", "Department not found")
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
    await ctx.db.flush()
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
    for row in existing:
        if row.ad_group_dn not in new_dns:
            await ctx.db.delete(row)
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
