"""DR9 / FR-G5 / FR-D5: who may set whose personal values (pure rule, no database)."""

import pytest

from app.errors import ApiError
from app.models import Employee, Grp
from app.permissions import Binding, Principal
from app.services.personal_values import authorize

GROUP = Grp(id=1, department_id=1, name="A1", litellm_team_id="A1")


def _p(upn: str, *bindings: Binding) -> Principal:
    return Principal(upn, upn, list(bindings))


ADMIN = Binding("platform_admin", "global", None)
REP = Binding("department_rep", "department", 1)
HEAD = Binding("group_head", "group", 1)


def _target(upn: str, status="active", group_id=1) -> Employee:
    return Employee(upn=upn, group_id=group_id, status=status)


def _code(actor, target, tier="member") -> str | None:
    try:
        return authorize(actor, target, GROUP, tier)
    except ApiError as e:
        return e.code


def test_head_sets_member_values():
    assert authorize(_p("h", HEAD), _target("m"), GROUP, "member") == "group_head"


def test_admin_sets_member_values():
    assert authorize(_p("a", ADMIN), _target("m"), GROUP, "member") == "platform_admin"


def test_admin_cannot_set_own_values():
    with pytest.raises(ApiError) as e:
        authorize(_p("a", ADMIN), _target("a"), GROUP, "platform_admin")
    assert e.value.code == "SELF_ALLOCATION_FORBIDDEN"
    assert e.value.details == {"owner_role": "platform_admin"}


def test_head_own_row_self_allocation_forbidden():
    with pytest.raises(ApiError) as e:
        authorize(_p("h", HEAD), _target("h"), GROUP, "group_head")
    assert (e.value.code, e.value.details) == (
        "SELF_ALLOCATION_FORBIDDEN",
        {"owner_role": "department_rep"},
    )


def test_rep_sets_group_head_values():
    assert authorize(_p("r", REP), _target("h"), GROUP, "group_head") == "department_rep"


def test_cohead_scope_forbidden():
    assert _code(_p("h2", HEAD), _target("h"), "group_head") == "SCOPE_FORBIDDEN"


def test_staff_head_cannot_set_rep_values():
    assert _code(_p("h", HEAD), _target("r"), "department_rep") == "SCOPE_FORBIDDEN"


def test_rep_cannot_set_rep_or_admin_values():
    assert _code(_p("r", REP), _target("r2"), "department_rep") == "SCOPE_FORBIDDEN"
    assert _code(_p("r", REP), _target("a"), "platform_admin") == "SCOPE_FORBIDDEN"


def test_admin_sets_rep_values():
    assert authorize(_p("a", ADMIN), _target("r"), GROUP, "department_rep") == "platform_admin"


def test_rep_cannot_set_plain_member_values():
    assert _code(_p("r", REP), _target("m")) == "SCOPE_FORBIDDEN"


def test_target_outside_group_or_left_scope_forbidden():
    assert _code(_p("h", HEAD), _target("m", group_id=2)) == "SCOPE_FORBIDDEN"
    assert _code(_p("h", HEAD), _target("m", status="left")) == "SCOPE_FORBIDDEN"
