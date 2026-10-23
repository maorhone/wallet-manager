"""Internal Systems: optional managing section (Phase 5.5).

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("production_system", sa.Column("managing_group_id", sa.BigInteger()))
    op.create_foreign_key(None, "production_system", "grp", ["managing_group_id"], ["id"])


def downgrade() -> None:
    op.drop_column("production_system", "managing_group_id")
