"""Phase 5.1: migration backfill and database-enforced group trees; no hierarchy API."""

import asyncio
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import create_database, make_session_factory, to_asyncpg_url
from app.models import Department, Grp, RoleBinding
from app.seed import seed


async def _group(conn, name, department_id=1, parent=None):
    return await conn.scalar(
        text(
            "INSERT INTO grp (name, litellm_team_id, department_id, parent_group_id) "
            "VALUES (:name, :name, :dept, :parent) RETURNING id"
        ),
        {"name": name, "dept": department_id, "parent": parent},
    )


async def _reject(conn, sql, params, constraint):
    with pytest.raises(IntegrityError, match=constraint):
        async with conn.begin_nested():
            await conn.execute(text(sql), params)


async def test_default_org_backfilled(pg_server):
    """Exercise an actual populated v1 upgrade, downgrade and re-upgrade in isolation."""
    await create_database(pg_server.get_uri(), "wm_hierarchy_migration")
    url = to_asyncpg_url(pg_server.get_uri(), database="wm_hierarchy_migration")

    def migrate(*args):
        subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            env={**os.environ, "DATABASE_URL": url},
            check=True,
            capture_output=True,
        )

    migrate("upgrade", "0006")
    db = create_async_engine(url)
    try:
        async with db.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO department (name, litellm_org_id, budget, prod_budget) "
                    "VALUES ('Department A', 'legacy-a', 123.45, 20), "
                    "('Department B', 'legacy-b', 76.55, NULL)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO grp (name, litellm_team_id, department_id, base_budget) "
                    "VALUES ('Group A', 'legacy-team', 1, 50.25)"
                )
            )
        for _ in range(2):
            migrate("upgrade", "head")
            async with db.connect() as conn:
                org = (await conn.execute(text("SELECT * FROM organization"))).mappings().one()
                assert org["departments_envelope"] == Decimal("200.00")
                assert org["systems_envelope"] == Decimal("0.00")
                assert org["budget_period"] == "1mo"
                assert org["systems_litellm_org_id"] is None
                assert org["created_at"].tzinfo is not None
                assert set(await conn.scalars(text("SELECT organization_id FROM department"))) == {
                    org["id"]
                }
                assert (
                    await conn.execute(text("SELECT base_budget, parent_group_id FROM grp"))
                ).one() == (Decimal("50.25"), None)
                assert await conn.scalar(text("SELECT count(*) FROM litellm_write")) == 0
            migrate("downgrade", "0006")
            await db.dispose()  # DDL replaces table OIDs; discard asyncpg's prepared statements.
        migrate("upgrade", "head")
    finally:
        await db.dispose()


async def test_group_parent_same_department(engine, seeded):
    async with engine.begin() as conn:
        parent = await _group(conn, "parent")
        child = await _group(conn, "child", parent=parent)
        foreign_child = await _group(conn, "foreign-child", department_id=2)
        await _reject(
            conn,
            "UPDATE grp SET parent_group_id = :parent WHERE id = :id",
            {"parent": parent, "id": foreign_child},
            "fk_grp_parent_department",
        )
        await _reject(
            conn,
            "UPDATE grp SET department_id = 2 WHERE id = :id",
            {"id": parent},
            "fk_grp_parent_department",
        )
        await _reject(
            conn,
            "UPDATE grp SET department_id = 2 WHERE id = :id",
            {"id": child},
            "fk_grp_parent_department",
        )
        await _reject(
            conn,
            "UPDATE grp SET parent_group_id = 99999 WHERE id = :id",
            {"id": child},
            "fk_grp_parent_department",
        )


async def test_cycle_rejected(engine, seeded):
    async with engine.begin() as conn:
        root = await _group(conn, "root")
        child = await _group(conn, "child", parent=root)
        leaf = await _group(conn, "leaf", parent=child)
        await _reject(
            conn,
            "UPDATE grp SET parent_group_id = id WHERE id = :id",
            {"id": root},
            "ck_grp_not_self_parent",
        )
        for parent in (child, leaf):
            await _reject(
                conn,
                "UPDATE grp SET parent_group_id = :parent WHERE id = :id",
                {"id": root, "parent": parent},
                "ck_grp_hierarchy",
            )
        # Validate the final statement too, including cycles made by multiple updated rows.
        a = await _group(conn, "a")
        b = await _group(conn, "b")
        await _reject(
            conn,
            "UPDATE grp SET parent_group_id = CASE id WHEN :a THEN :b ELSE :a END "
            "WHERE id IN (:a, :b)",
            {"a": a, "b": b},
            "ck_grp_hierarchy",
        )


async def test_depth_limit(engine, seeded):
    """A root group is depth 1; descendants of a moved subtree count as well."""
    async with engine.begin() as conn:
        root = await _group(conn, "level1")
        parent = root
        for level in range(2, 5):
            parent = await _group(conn, f"level{level}", parent=parent)
        with pytest.raises(IntegrityError, match="ck_grp_hierarchy"):
            async with conn.begin_nested():
                await _group(conn, "level5", parent=parent)
        other = await _group(conn, "other-root")
        await _reject(
            conn,
            "UPDATE grp SET parent_group_id = :parent WHERE id = :id",
            {"id": root, "parent": other},
            "ck_grp_hierarchy",
        )
        # Detaching and subsequently reparenting a smaller subtree stays valid.
        await conn.execute(
            text("UPDATE grp SET parent_group_id = NULL WHERE id = :id"), {"id": parent}
        )
        await conn.execute(
            text("UPDATE grp SET parent_group_id = :parent WHERE id = :id"),
            {"id": root, "parent": other},
        )


async def test_concurrent_reparent_cannot_create_cycle(engine, seeded):
    async with engine.begin() as conn:
        a = await _group(conn, "a")
        b = await _group(conn, "b")
    ready = asyncio.Barrier(2)

    async def reparent(child, parent):
        try:
            async with engine.begin() as conn:
                await ready.wait()
                await conn.execute(
                    text("UPDATE grp SET parent_group_id = :parent WHERE id = :id"),
                    {"id": child, "parent": parent},
                )
            return "applied"
        except IntegrityError as exc:
            assert "ck_grp_hierarchy" in str(exc)
            return "rejected"

    assert sorted(await asyncio.gather(reparent(a, b), reparent(b, a))) == ["applied", "rejected"]


async def test_seed_default_org_and_allocator(seeded, settings, tmp_path):
    from app.models import Organization

    data = yaml.safe_load(Path(settings.seed_file).read_text())
    data["role_bindings"].append(
        {
            "group": "CN=org-allocators,DC=corp,DC=example",
            "role": "org_allocator",
            "scope_kind": "organization",
            "scope": "default",
        }
    )
    path = tmp_path / "seed.yaml"
    path.write_text(yaml.safe_dump(data))
    await seed(seeded, path)
    await seed(seeded, path)
    async with seeded() as session:
        org = (await session.scalars(select(Organization))).one()
        assert isinstance(org.departments_envelope, Decimal)
        assert org.systems_envelope == Decimal("0.00")
        assert {d.organization_id for d in await session.scalars(select(Department))} == {org.id}
        assert all(g.parent_group_id is None for g in await session.scalars(select(Grp)))
        binding = (
            await session.scalars(select(RoleBinding).where(RoleBinding.role == "org_allocator"))
        ).one()
        assert (binding.scope_kind, binding.scope_id) == ("organization", org.id)
        other = Organization(name="Another organization")
        session.add(other)
        await session.flush()
        assert other.id > org.id
        assert other.departments_envelope == other.systems_envelope == Decimal("0.00")


async def test_seed_initializes_empty_migrated_org(engine, settings):
    """A fresh migration has already created the default root before dev seeding runs."""
    from app.models import Organization

    factory = make_session_factory(engine)
    async with factory() as session, session.begin():
        session.add(Organization(name="Default organization"))
    await seed(factory, settings.seed_file)
    async with factory() as session:
        org = await session.get(Organization, 1)
        budgets = await session.scalars(select(Department.budget))
        assert org.departments_envelope == sum(budgets, Decimal(0))
