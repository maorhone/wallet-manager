"""Production organizations/systems (FR-K1, FR-K2, DR4) and service_key constraints (Step 3.4).

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("department", sa.Column("prod_litellm_org_id", sa.Text(), nullable=True))
    op.add_column(
        "department", sa.Column("prod_budget", sa.Numeric(precision=18, scale=2), nullable=True)
    )
    op.add_column(
        "department",
        sa.Column("prod_budget_period", sa.Text(), server_default="1mo", nullable=False),
    )
    op.create_unique_constraint(
        "department_prod_litellm_org_id_key", "department", ["prod_litellm_org_id"]
    )

    op.create_table(
        "production_system",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("department_id", sa.BigInteger(), nullable=False),
        sa.Column("litellm_team_id", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("budget", sa.Numeric(precision=18, scale=2), server_default="0", nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),  # fmt: skip
        sa.ForeignKeyConstraint(["department_id"], ["department.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("litellm_team_id"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_production_system_status"),
    )

    op.add_column(
        "service_key",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),  # fmt: skip
    )
    op.add_column("service_key", sa.Column("created_by", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_service_key_owner_kind", "service_key", "owner_kind IN ('group', 'system')"
    )
    op.create_check_constraint(
        "ck_service_key_status",
        "service_key",
        "status IN ('pending', 'active', 'failed', 'revoked')",
    )
    op.create_index(
        "uq_service_key_token_hash",
        "service_key",
        ["litellm_token_hash"],
        unique=True,
        postgresql_where=sa.text("litellm_token_hash IS NOT NULL"),
    )
    op.create_index("ix_service_key_owner", "service_key", ["owner_kind", "owner_id"])


def downgrade() -> None:
    op.drop_index("ix_service_key_owner", table_name="service_key")
    op.drop_index("uq_service_key_token_hash", table_name="service_key")
    op.drop_constraint("ck_service_key_status", "service_key")
    op.drop_constraint("ck_service_key_owner_kind", "service_key")
    op.drop_column("service_key", "created_by")
    op.drop_column("service_key", "created_at")

    op.drop_table("production_system")

    op.drop_constraint("department_prod_litellm_org_id_key", "department")
    op.drop_column("department", "prod_budget_period")
    op.drop_column("department", "prod_budget")
    op.drop_column("department", "prod_litellm_org_id")
