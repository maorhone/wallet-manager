import pytest
from sqlalchemy import select

from app import permissions as perm
from app.directory import FixtureDirectory
from app.models import Department, Grp
from app.permissions import Principal, load_bindings


async def _principal(factory, directory, who: str) -> Principal:
    upn = f"{who}@corp.example"
    async with factory() as s:
        return Principal(upn, who, await load_bindings(s, directory.groups_of(upn)))


# helper -> (kind, expected role per persona); a missing persona is not visible (404),
# "403" means visible but not permitted. A1 = group of dana; A2 = group of noa; C1 is in D2.
ADMIN, REP, PMO, HEAD, VIEWER = "platform_admin", "department_rep", "pmo", "group_head", "viewer"
MATRIX = {
    "can_view_department": ("dept", {"admin": ADMIN, "rep": REP, "pmo": PMO, "noa": VIEWER,
                                     "viewer": VIEWER, "dana": 404, "dani": 404}),
    "can_edit_split": ("dept", {"admin": ADMIN, "rep": REP, "pmo": 403, "noa": 403,
                                "viewer": 403, "dana": 404, "dani": 404}),
    "can_view_group": ("A1", {"admin": ADMIN, "rep": REP, "pmo": PMO, "dana": HEAD, "dani": 404,
                              "noa": VIEWER, "viewer": VIEWER, "avi": 404}),
    "can_view_members": ("A1", {"admin": ADMIN, "rep": REP, "pmo": 403, "dana": HEAD,
                                "dani": 404, "noa": 403, "viewer": 403}),
    "can_edit_group_pots_limits": ("A1", {"admin": ADMIN, "rep": REP, "pmo": 403, "dana": 403,
                                          "noa": 403, "dani": 404}),
    "can_edit_member_defaults": ("A1", {"admin": ADMIN, "rep": 403, "pmo": 403, "dana": HEAD,
                                        "noa": 403, "dani": 404}),
    "can_write_members": ("A1", {"admin": ADMIN, "rep": REP, "pmo": 403, "dana": HEAD,
                                 "noa": 403, "viewer": 403, "dani": 404}),
    # noa heads A2, so on A2 she is HEAD where she was only a viewer on A1
    "can_write_members@A2": ("A2", {"noa": HEAD, "dana": 404, "rep": REP}),
}  # fmt: skip
CASES = [(h, who, exp) for h, (_, exps) in MATRIX.items() for who, exp in exps.items()]


@pytest.mark.parametrize(("helper", "who", "expected"), CASES)
async def test_matrix_12_2(seeded, helper, who, expected):
    directory = FixtureDirectory("seed/dev.yaml")
    p = await _principal(seeded, directory, who)
    kind, _ = MATRIX[helper]
    fn = getattr(perm, helper.split("@")[0])
    async with seeded() as s:
        if kind == "dept":
            ident = (
                await s.execute(select(Department.id).where(Department.name == "Department D1"))
            ).scalar_one()
        else:
            ident = (
                await s.execute(select(Grp.id).where(Grp.litellm_team_id == kind))
            ).scalar_one()
        if isinstance(expected, int):
            with pytest.raises(perm.ApiError) as exc:
                await fn(s, p, ident)
            assert exc.value.status == expected
        else:
            assert await fn(s, p, ident) == expected


async def test_unknown_entity_404_and_dept_budget_admin_only(seeded):
    directory = FixtureDirectory("seed/dev.yaml")
    async with seeded() as s:
        with pytest.raises(perm.ApiError) as exc:
            await perm.can_view_group(s, await _principal(seeded, directory, "admin"), 9999)
        assert exc.value.status == 404
    assert perm.can_edit_dept_budget(await _principal(seeded, directory, "admin")) == ADMIN
    with pytest.raises(perm.ApiError) as exc:
        perm.can_edit_dept_budget(await _principal(seeded, directory, "rep"))
    assert exc.value.status == 403


@pytest.mark.parametrize(
    ("who", "tier"),
    [
        ("admin", "platform_admin"),
        ("rep", "department_rep"),
        ("dana", "group_head"),
        ("noa", "group_head"),
        ("pmo", "member"),
        ("dani", "member"),
        ("viewer", "member"),
        ("nobody", "member"),
    ],
)
async def test_target_tier(seeded, who, tier):
    directory = FixtureDirectory("seed/dev.yaml")
    async with seeded() as s:
        assert await perm.target_tier(s, directory, f"{who}@corp.example") == tier
