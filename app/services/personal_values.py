"""DR9, FR-G5, FR-D5: who may set whose personal values."""

from app.errors import ApiError, scope_forbidden, self_allocation_forbidden
from app.models import Employee, Grp
from app.permissions import ADMIN, HEAD, REP, Principal

# Role that sets the values of each target tier (DR9); an admin's owner is another admin.
OWNER_ROLE = {
    "member": "group_head",
    "group_head": "department_rep",
    "department_rep": "platform_admin",
    "platform_admin": "platform_admin",
}


def authorize(principal: Principal, target: Employee, group: Grp, tier: str) -> str:
    """Return the actor role that authorizes the change, or raise 422.

    ``tier`` is the highest role the target holds (`permissions.target_tier`).
    """
    if target.upn == principal.upn:
        err = self_allocation_forbidden()
        err.details = {"owner_role": OWNER_ROLE[tier]}
        raise err
    if target.group_id != group.id or target.status == "left":
        raise scope_forbidden("Target is not an active member of the group")
    roles = principal.group_roles(group.id, group.department_id)
    allowed = {
        "member": (ADMIN, HEAD),
        "group_head": (ADMIN, REP),
        "department_rep": (ADMIN,),
        "platform_admin": (ADMIN,),
    }[tier]
    for role in allowed:
        if role in roles:
            return role
    raise scope_forbidden("You may not set this person's values")


def authorize_rows(principal: Principal, rows: list[tuple[Employee, str]], group: Grp) -> list[str]:
    """Authorize every (employee, tier); raise one error (SELF over SCOPE) listing failing rows."""
    errors: list[ApiError] = []
    failed = []
    roles = []
    for target, tier in rows:
        try:
            roles.append(authorize(principal, target, group, tier))
        except ApiError as e:
            errors.append(e)
            failed.append({"upn": target.upn, "code": e.code})
    if not errors:
        return roles
    worst = next((e for e in errors if e.code == "SELF_ALLOCATION_FORBIDDEN"), errors[0])
    worst.details = {**(worst.details or {}), "rows": failed}
    raise worst
