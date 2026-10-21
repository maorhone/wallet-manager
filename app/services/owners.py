"""FR-E3, DR9: who can change a person's values, with names from the directory."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.directory import Directory, DirectoryUnavailable
from app.models import Grp, RoleBinding
from app.permissions import Principal, target_tier, tier_from_bindings
from app.services.personal_values import OWNER_ROLE


async def holders(
    db: AsyncSession, directory: Directory, role: str, scope_kind: str, scope_id: int | None
) -> list[dict]:
    """People holding ``role`` on a scope: the directory members of the bound AD groups.

    Degrades to ``[]`` when the directory is unreachable (§5.4), so a wallet with a known
    owner role still renders without names.
    """
    dns = await db.scalars(
        select(RoleBinding.ad_group_dn).where(
            RoleBinding.role == role,
            RoleBinding.scope_kind == scope_kind,
            RoleBinding.scope_id.is_(None)
            if scope_id is None
            else RoleBinding.scope_id == scope_id,
        )
    )
    try:
        upns = sorted({u for dn in dns for u in directory.members_of(dn)})
        return [{"upn": u, "display_name": directory.display_name(u) or u} for u in upns]
    except DirectoryUnavailable:
        return []


async def owner_of(
    db: AsyncSession,
    directory: Directory,
    upn: str,
    group: Grp,
    cache: dict | None = None,
    principal: Principal | None = None,
) -> dict:
    """``{role, people}`` for the person who sets ``upn``'s values (empty people if unknown).

    When ``upn`` is ``principal``'s own (the wallet read path), the tier comes from the
    session's already-loaded bindings instead of a fresh directory call, so a full directory
    outage degrades to empty ``people`` instead of a 503 (§5.4). Any other ``upn`` still needs
    a live ``target_tier`` lookup.
    """
    tier = (
        tier_from_bindings(principal.bindings)
        if principal is not None and principal.upn == upn
        else await target_tier(db, directory, upn)
    )
    role = OWNER_ROLE[tier]
    scope = {
        "group_head": ("group", group.id),
        "department_rep": ("department", group.department_id),
    }.get(role, ("global", None))
    cache = {} if cache is None else cache
    if (role, scope) not in cache:
        cache[(role, scope)] = await holders(db, directory, role, *scope)
    return {"role": role, "people": cache[(role, scope)]}
