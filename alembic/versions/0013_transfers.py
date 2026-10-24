"""Transfers of unused balances (Phase 5.9).

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

KINDS = "('employee', 'initiative', 'group', 'system')"


def upgrade() -> None:
    op.create_table(
        "transfer",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("group_id", sa.BigInteger(), sa.ForeignKey("grp.id")),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("target_kind", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("write_ids", sa.ARRAY(sa.BigInteger()), server_default="{}", nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(f"source_kind IN {KINDS}", name="ck_transfer_source_kind"),
        sa.CheckConstraint(f"target_kind IN {KINDS}", name="ck_transfer_target_kind"),
        sa.CheckConstraint("amount > 0", name="ck_transfer_amount"),
    )


def downgrade() -> None:
    op.drop_table("transfer")
