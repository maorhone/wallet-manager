"""Rate card and token columns on the spend snapshot.

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in ("prompt_tokens", "completion_tokens"):
        op.add_column(
            "spend_snapshot",
            sa.Column(col, sa.BigInteger(), server_default="0", nullable=False),
        )
    op.create_table(
        "rate_card",
        sa.Column("model", sa.Text(), primary_key=True),
        sa.Column("input_per_1m", sa.Numeric(18, 6), nullable=False),
        sa.Column("output_per_1m", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency", sa.Text(), server_default="USD", nullable=False),
        sa.Column(
            "effective_from", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
    )  # fmt: skip


def downgrade() -> None:
    op.drop_table("rate_card")
    op.drop_column("spend_snapshot", "completion_tokens")
    op.drop_column("spend_snapshot", "prompt_tokens")
