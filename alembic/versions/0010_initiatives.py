"""Internal Usage initiatives (Phase 5.6).

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "initiative",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("owner_upn", sa.Text(), nullable=False),
        sa.Column("group_id", sa.BigInteger(), sa.ForeignKey("grp.id"), nullable=False),
        sa.Column("department_id", sa.BigInteger(), sa.ForeignKey("department.id"), nullable=False),
        sa.Column("period_start", sa.Date()),
        sa.Column("period_end", sa.Date()),
        sa.Column("budget", sa.Numeric(18, 2), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.Text()),
        sa.UniqueConstraint("department_id", "name", name="uq_initiative_department_name"),
        sa.CheckConstraint("status IN ('active', 'closed')", name="ck_initiative_status"),
    )
    op.drop_constraint("ck_service_key_owner_kind", "service_key")
    op.create_check_constraint(
        "ck_service_key_owner_kind",
        "service_key",
        "owner_kind IN ('group', 'system', 'initiative')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_service_key_owner_kind", "service_key")
    op.create_check_constraint(
        "ck_service_key_owner_kind", "service_key", "owner_kind IN ('group', 'system')"
    )
    op.drop_table("initiative")
