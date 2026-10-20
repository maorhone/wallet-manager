"""Preserve spend precision when returning grant money.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("grant", "grant_share"):
        op.alter_column(table, "returned", type_=sa.Numeric(18, 4), existing_type=sa.Numeric(18, 2))
    # Repair previously rounded returns using the exact usage retained on closed rows.
    op.execute("UPDATE grant_share SET returned = amount - used WHERE status = 'closed'")
    op.execute("""UPDATE "grant" g SET returned = CASE WHEN status = 'closed'
        THEN amount - used ELSE (SELECT COALESCE(SUM(s.returned), 0)
        FROM grant_share s WHERE s.grant_id = g.id AND s.status = 'closed') END""")


def downgrade() -> None:
    for table in ("grant", "grant_share"):
        op.alter_column(table, "returned", type_=sa.Numeric(18, 2), existing_type=sa.Numeric(18, 4))
