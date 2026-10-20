import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import (
    Department,
    Employee,
    Grant,
    GrantShare,
    Grp,
    LitellmWrite,
    Policy,
    RoleBinding,
)
from app.services.lifecycle import JOINER
from app.writes import WriteContext

_DEPT_FIELDS = ("name", "ad_group_dn", "budget", "pmo_pool", "reserve")
_GROUP_FIELDS = ("name", "ad_group_dn", "kind", "base_budget")


async def _upsert(session: AsyncSession, model, key_col: str, key: str, values: dict):
    row = (
        await session.execute(select(model).where(getattr(model, key_col) == key))
    ).scalar_one_or_none()
    if row is None:
        row = model(**{key_col: key})
        session.add(row)
    for k, v in values.items():
        setattr(row, k, v)
    await session.flush()
    return row


async def _provision(session: AsyncSession, settings: Settings) -> None:
    """Enqueue the LiteLLM objects the seeded rows need: orgs, teams, then the Joiner sequence."""
    ctx = WriteContext(session, settings, "seed", str(uuid.uuid4()), "initial provisioning")
    depts = await session.scalars(select(Department).order_by(Department.id))
    steps = [("department", str(d.id), "/organization/new") for d in depts]
    groups = await session.scalars(select(Grp).order_by(Grp.id))
    steps += [("group", str(g.id), "/team/new") for g in groups]
    employees = await session.scalars(
        select(Employee).where(Employee.group_id.is_not(None)).order_by(Employee.upn)
    )
    steps += [("employee", e.upn, ep) for e in employees for ep in JOINER]
    known = await session.execute(
        select(LitellmWrite.entity, LitellmWrite.entity_id, LitellmWrite.endpoint)
    )
    done = {tuple(r) for r in known}
    for entity, entity_id, endpoint in steps:
        if (entity, entity_id, endpoint) in done:
            continue
        ctx.audit(entity, entity_id, None, {"enqueue": endpoint}, "sync_service")
        await ctx.enqueue(entity, entity_id, endpoint)


async def _seed_grants(
    session: AsyncSession, settings: Settings, rows: list[dict], group_ids, dept_ids
) -> None:
    """Dev grants (active now) with their shares; skipped when the project already exists."""
    ctx = WriteContext(session, settings, "seed", str(uuid.uuid4()), "dev grants")
    now = datetime.now(UTC)
    for row in rows:
        group = await session.get(Grp, group_ids[row["group"]])
        if await session.scalar(
            select(Grant.id).where(
                Grant.group_id == group.id, Grant.project_ref == row["project_ref"]
            )
        ):
            continue
        grant = Grant(
            department_id=group.department_id, group_id=group.id, amount=row["amount"],
            project_ref=row["project_ref"], purpose=row.get("purpose"), status="active",
            starts_at=now, expires_at=now + timedelta(days=row["expires_in_days"]),
            created_by="seed",
        )  # fmt: skip
        session.add(grant)
        await session.flush()
        await ctx.enqueue("group", group.id, "/team/update")
        for s in row.get("shares", []):
            session.add(
                GrantShare(
                    grant_id=grant.id, upn=s["upn"], amount=s["amount"],
                    expires_at=now + timedelta(days=s["expires_in_days"]),
                )
            )  # fmt: skip
            await ctx.enqueue("employee", s["upn"], "/team/member_update")


async def seed(
    factory: async_sessionmaker[AsyncSession],
    path: str | Path,
    reset: bool = False,
    settings: Settings | None = None,
):
    """Load WM rows from the fixture. Idempotent; ``reset`` truncates every table first.

    With ``settings`` it also enqueues the LiteLLM provisioning (once per object).
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    async with factory() as session, session.begin():
        if reset:
            names = (
                await session.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                        "AND tablename <> 'alembic_version'"
                    )
                )
            ).scalars()
            tables = ", ".join(f'"{n}"' for n in names)
            await session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        for key, value in data.get("policies", {}).items():
            await session.merge(Policy(key=key, value=value))

        dept_ids: dict[str, int] = {}
        group_ids: dict[str, int] = {}
        group_by_dn: dict[str, int] = {}
        for d in data["departments"]:
            dept = await _upsert(
                session, Department, "litellm_org_id", d["key"],
                {f: d[f] for f in _DEPT_FIELDS if f in d},
            )  # fmt: skip
            dept_ids[d["key"]] = dept.id
            for g in d["groups"]:
                grp = await _upsert(
                    session, Grp, "litellm_team_id", g["key"],
                    {"department_id": dept.id, **{f: g[f] for f in _GROUP_FIELDS if f in g}},
                )  # fmt: skip
                group_ids[g["key"]] = grp.id
                group_by_dn[g["ad_group_dn"]] = grp.id

        for u in data.get("users", []):
            if u.get("provision") is False:
                continue  # directory-only user, onboarded through the API
            group_id = next((group_by_dn[dn] for dn in u["groups"] if dn in group_by_dn), None)
            pending = {}
            if u.get("pending"):
                pending = {
                    "status": "pending_placement",
                    "pending_department_id": dept_ids.get(u.get("pending_department")),
                    "pending_reason": u["pending_reason"],
                    "pending_since": datetime.now(UTC),
                }
            await session.merge(
                Employee(
                    upn=u["upn"],
                    litellm_user_id=u["upn"],
                    display_name=u["display_name"],
                    group_id=None if pending else group_id,
                    base_cap=u.get("base_cap", 0),
                    **pending,
                )  # fmt: skip
            )

        await session.execute(delete(RoleBinding))
        scope_ids = {"global": {None: None}, "department": dept_ids, "group": group_ids}
        for b in data.get("role_bindings", []):
            session.add(
                RoleBinding(
                    ad_group_dn=b["group"],
                    role=b["role"],
                    scope_kind=b["scope_kind"],
                    scope_id=scope_ids[b["scope_kind"]][b.get("scope")],
                )  # fmt: skip
            )
        if settings is not None:
            await _provision(session, settings)
            if settings.seed_grants:
                await _seed_grants(session, settings, data.get("grants", []), group_ids, dept_ids)
