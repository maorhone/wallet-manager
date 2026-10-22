"""WM organization root and nestable, department-local groups (Phase 5.1).

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organization",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("departments_envelope", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("systems_envelope", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("budget_period", sa.Text(), server_default="1mo", nullable=False),
        sa.Column("systems_litellm_org_id", sa.Text(), nullable=True, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("departments_envelope >= 0", name="ck_org_departments_envelope"),
        sa.CheckConstraint("systems_envelope >= 0", name="ck_org_systems_envelope"),
    )
    # Internal production systems remain department-funded. No org-system money is invented.
    op.execute(
        "INSERT INTO organization (name, departments_envelope) "
        "SELECT 'Default organization', COALESCE(sum(budget), 0) FROM department"
    )
    op.add_column(
        "department",
        sa.Column("organization_id", sa.BigInteger(), server_default="1", nullable=False),
    )
    op.create_foreign_key(
        "department_organization_id_fkey", "department", "organization", ["organization_id"], ["id"]
    )
    op.create_index("ix_department_organization_id", "department", ["organization_id"])
    op.add_column("grp", sa.Column("parent_group_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint("uq_grp_id_department", "grp", ["id", "department_id"])
    op.create_foreign_key(
        "fk_grp_parent_department",
        "grp",
        "grp",
        ["parent_group_id", "department_id"],
        ["id", "department_id"],
    )
    op.create_check_constraint("ck_grp_not_self_parent", "grp", "parent_group_id <> id")
    op.create_index("ix_grp_parent_group_id", "grp", ["parent_group_id"])

    # Serialize hierarchy writes using the existing per-department advisory lock namespace.
    # Lock before mutation; the AFTER statement query then sees committed concurrent writes
    # and the complete new subtree, including multi-row updates. No API is introduced here.
    op.execute("""
        CREATE FUNCTION lock_grp_hierarchy() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE dept_id bigint;
        BEGIN
            FOR dept_id IN
                SELECT DISTINCT d FROM unnest(ARRAY[
                    NEW.department_id,
                    CASE WHEN TG_OP = 'UPDATE' THEN OLD.department_id ELSE NEW.department_id END
                ]) AS t(d) ORDER BY d
            LOOP
                PERFORM pg_advisory_xact_lock(1, dept_id::integer);
            END LOOP;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER grp_hierarchy_lock
        BEFORE INSERT OR UPDATE OF parent_group_id, department_id ON grp
        FOR EACH ROW EXECUTE FUNCTION lock_grp_hierarchy()
    """)
    op.execute("""
        CREATE FUNCTION check_grp_hierarchy() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                WITH RECURSIVE ancestors AS (
                    SELECT id, parent_group_id, 1 AS depth FROM grp
                    UNION ALL
                    SELECT p.id, p.parent_group_id, a.depth + 1
                    FROM ancestors a JOIN grp p ON p.id = a.parent_group_id
                    WHERE a.depth < 5
                )
                SELECT 1 FROM ancestors WHERE depth > 4
            ) THEN
                RAISE EXCEPTION 'ck_grp_hierarchy: groups must be acyclic and depth <= 4'
                    USING ERRCODE = '23514', CONSTRAINT = 'ck_grp_hierarchy';
            END IF;
            RETURN NULL;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER grp_hierarchy_check
        AFTER INSERT OR UPDATE OF parent_group_id, department_id ON grp
        FOR EACH STATEMENT EXECUTE FUNCTION check_grp_hierarchy()
    """)
    # role_binding.role and scope_kind are already unrestricted text: organization-scoped
    # org_allocator bindings need no column/constraint change. Seed resolves their scope IDs.


def downgrade() -> None:
    op.execute("DROP TRIGGER grp_hierarchy_check ON grp")
    op.execute("DROP FUNCTION check_grp_hierarchy()")
    op.execute("DROP TRIGGER grp_hierarchy_lock ON grp")
    op.execute("DROP FUNCTION lock_grp_hierarchy()")
    op.drop_index("ix_grp_parent_group_id", table_name="grp")
    op.drop_constraint("ck_grp_not_self_parent", "grp")
    op.drop_constraint("fk_grp_parent_department", "grp")
    op.drop_constraint("uq_grp_id_department", "grp")
    op.drop_column("grp", "parent_group_id")
    op.drop_index("ix_department_organization_id", table_name="department")
    op.drop_constraint("department_organization_id_fkey", "department")
    op.drop_column("department", "organization_id")
    op.drop_table("organization")
