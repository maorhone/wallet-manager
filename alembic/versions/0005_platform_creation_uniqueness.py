"""Enforce platform DN uniqueness and one staff group per department (D13).

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("uq_department_ad_group_dn", "department", ["ad_group_dn"], unique=True)
    op.create_index("uq_grp_ad_group_dn", "grp", ["ad_group_dn"], unique=True)
    op.create_index(
        "uq_grp_department_staff",
        "grp",
        ["department_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'department_staff'"),
    )


def downgrade() -> None:
    op.drop_index("uq_grp_department_staff", table_name="grp")
    op.drop_index("uq_grp_ad_group_dn", table_name="grp")
    op.drop_index("uq_department_ad_group_dn", table_name="department")
