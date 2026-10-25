"""Transfer lifecycle state and an indexed source-write link (review round 3).

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transfer", sa.Column("state", sa.Text(), nullable=False, server_default="applied")
    )
    op.add_column("transfer", sa.Column("first_write_id", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        "ck_transfer_state",
        "transfer",
        "state IN ('pending', 'applied', 'failed', 'compensated')",
    )
    op.create_index("ix_transfer_first_write_id", "transfer", ["first_write_id"])
    op.create_index("ix_transfer_target", "transfer", ["target_kind", "target_id", "state"])
    op.execute(
        "UPDATE transfer SET first_write_id = write_ids[1], state = 'pending' "
        "WHERE cardinality(write_ids) > 0 AND EXISTS ("
        "SELECT 1 FROM litellm_write w WHERE w.id = ANY(transfer.write_ids) "
        "AND w.status <> 'applied')"
    )
    op.execute(
        "UPDATE transfer SET first_write_id = write_ids[1] "
        "WHERE cardinality(write_ids) > 0 AND source_kind <> 'initiative'"
    )


def downgrade() -> None:
    op.drop_index("ix_transfer_target", table_name="transfer")
    op.drop_index("ix_transfer_first_write_id", table_name="transfer")
    op.drop_constraint("ck_transfer_state", "transfer", type_="check")
    op.drop_column("transfer", "first_write_id")
    op.drop_column("transfer", "state")
