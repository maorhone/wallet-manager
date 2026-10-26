"""Backfill retired key reservations for non-initiative keys (review round 6, B1b).

Revision ID: 0017
Revises: 0016
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 0012 backfilled initiative keys only; system (and group) keys rotated before
    # Phase 5 also left former upstream credentials. Every extra generation attempt, failed
    # timeouts included, keeps reserving the key's cap. Idempotent: only rows still at zero.
    op.execute("""
        UPDATE service_key AS k
        SET retired_budget = GREATEST((
            SELECT COUNT(*) - 1 FROM litellm_write AS w
            WHERE w.entity = 'service_key' AND w.entity_id = k.id::text
              AND w.endpoint = '/key/service-account/generate'
        ), 0) * COALESCE(k.max_budget, 0)
        WHERE k.owner_kind <> 'initiative' AND k.retired_budget = 0
    """)


def downgrade() -> None:
    pass
