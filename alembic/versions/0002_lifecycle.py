"""member lifecycle: pending placement, chained outbox rows, no AD sync tables (SPEC §8.2 v0.3)

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("sync_action")
    op.drop_table("sync_run")

    op.drop_constraint("ck_employee_status", "employee")
    op.create_check_constraint(
        "ck_employee_status",
        "employee",
        "status IN ('active', 'blocked', 'left', 'pending_placement')",
    )
    op.add_column("employee", sa.Column("pending_department_id", sa.BigInteger(), nullable=True))
    op.add_column("employee", sa.Column("pending_reason", sa.Text(), nullable=True))
    op.add_column("employee", sa.Column("pending_since", sa.DateTime(timezone=True)))
    op.create_foreign_key(
        "employee_pending_department_id_fkey", "employee", "department",
        ["pending_department_id"], ["id"],
    )  # fmt: skip
    op.create_check_constraint(
        "ck_employee_pending_reason",
        "employee",
        "pending_reason IS NULL OR pending_reason IN "
        "('no_budget_group', 'multiple_budget_groups', 'not_in_directory')",
    )

    op.add_column("litellm_write", sa.Column("depends_on", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "litellm_write_depends_on_fkey", "litellm_write", "litellm_write", ["depends_on"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("litellm_write_depends_on_fkey", "litellm_write")
    op.drop_column("litellm_write", "depends_on")

    op.drop_constraint("ck_employee_pending_reason", "employee")
    op.drop_constraint("employee_pending_department_id_fkey", "employee")
    op.drop_column("employee", "pending_since")
    op.drop_column("employee", "pending_reason")
    op.drop_column("employee", "pending_department_id")
    op.drop_constraint("ck_employee_status", "employee")
    op.create_check_constraint(
        "ck_employee_status", "employee", "status IN ('active', 'blocked', 'left')"
    )

    op.create_table(
        "sync_run",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dry_run", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("aborted_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )  # fmt: skip
    op.create_table(
        "sync_action",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("upn", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["sync_run.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
