"""Requests center (Phase 5.7).

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "request",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("scope_kind", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.BigInteger(), nullable=False),
        sa.Column("requester_upn", sa.Text(), nullable=False),
        sa.Column("handler_role", sa.Text(), nullable=False),
        sa.Column("handler_scope", sa.BigInteger()),
        sa.Column("status", sa.Text(), server_default="submitted", nullable=False),
        sa.Column("amount", sa.Numeric(18, 2)),
        sa.Column("application_write_id", sa.BigInteger(), sa.ForeignKey("litellm_write.id")),
        sa.Column("payload", JSONB(), server_default="{}", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "type IN ('credits', 'initiative', 'throughput')", name="ck_request_type"
        ),
        sa.CheckConstraint(
            "status IN ('submitted', 'forwarded', 'approved', 'rejected', 'applied', "
            "'pending_apply', 'failed')",
            name="ck_request_status",
        ),
    )
    op.create_table(
        "request_event",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.BigInteger(), sa.ForeignKey("request.id"), nullable=False),
        sa.Column("actor_upn", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("request_event")
    op.drop_table("request")
