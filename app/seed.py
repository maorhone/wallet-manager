from pathlib import Path

import yaml
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Department, Employee, Grp, Policy, RoleBinding

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


async def seed(factory: async_sessionmaker[AsyncSession], path: str | Path, reset: bool = False):
    """Load WM rows from the fixture. Idempotent; ``reset`` truncates every table first."""
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
            group_id = next((group_by_dn[dn] for dn in u["groups"] if dn in group_by_dn), None)
            await session.merge(
                Employee(
                    upn=u["upn"],
                    litellm_user_id=u["upn"],
                    display_name=u["display_name"],
                    group_id=group_id,
                    base_cap=u.get("base_cap", 0),
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
