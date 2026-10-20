"""indexes for grants, shares and notification de-duplication (no new tables or columns)

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_grant_status_starts", "grant", ["status", "starts_at"])
    op.create_index("ix_grant_status_expires", "grant", ["status", "expires_at"])
    op.create_index("ix_grant_share_status_expires", "grant_share", ["status", "expires_at"])
    op.create_index("ix_grant_share_upn_status", "grant_share", ["upn", "status"])
    op.execute(
        "CREATE UNIQUE INDEX uq_grant_share_active ON grant_share (grant_id, upn) "
        "WHERE status = 'active'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_notification_dedupe ON notification (upn, kind, (payload->>'key'))"
    )


def downgrade() -> None:
    op.drop_index("uq_notification_dedupe", "notification")
    op.drop_index("uq_grant_share_active", "grant_share")
    op.drop_index("ix_grant_share_upn_status", "grant_share")
    op.drop_index("ix_grant_share_status_expires", "grant_share")
    op.drop_index("ix_grant_status_expires", "grant")
    op.drop_index("ix_grant_status_starts", "grant")
