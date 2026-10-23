"""Org Systems: production systems funded from the organization's systems envelope (Phase 5.4).

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "production_system",
        sa.Column("funding", sa.Text(), server_default="department", nullable=False),
    )
    op.add_column("production_system", sa.Column("organization_id", sa.BigInteger()))
    op.add_column("production_system", sa.Column("owner_upn", sa.Text()))
    op.add_column("production_system", sa.Column("responsible_department_id", sa.BigInteger()))
    op.create_foreign_key(None, "production_system", "organization", ["organization_id"], ["id"])
    op.create_foreign_key(
        None, "production_system", "department", ["responsible_department_id"], ["id"]
    )
    op.alter_column("production_system", "department_id", nullable=True)
    op.create_check_constraint(
        "ck_production_system_funding",
        "production_system",
        "funding IN ('department', 'organization')",
    )
    op.create_check_constraint(
        "ck_production_system_funder",
        "production_system",
        "(funding = 'department' AND department_id IS NOT NULL)"
        " OR (funding = 'organization' AND organization_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_production_system_funder", "production_system")
    op.drop_constraint("ck_production_system_funding", "production_system")
    op.execute("DELETE FROM production_system WHERE department_id IS NULL")
    op.alter_column("production_system", "department_id", nullable=False)
    op.drop_column("production_system", "responsible_department_id")
    op.drop_column("production_system", "owner_upn")
    op.drop_column("production_system", "organization_id")
    op.drop_column("production_system", "funding")
