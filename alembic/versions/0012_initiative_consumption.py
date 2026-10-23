"""Preserve service-key consumption across initiative key generations."""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_key",
        sa.Column("retired_budget", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )

    # Every previously attempted rotation may have created another upstream credential.
    # Limits are immutable; preserve the full cap of each former generation. A failed
    # timeout is intentionally reserved too because creation can have succeeded upstream.
    op.execute("""
        UPDATE service_key AS k
        SET retired_budget = GREATEST((
            SELECT COUNT(*) - 1 FROM litellm_write AS w
            WHERE w.entity = 'service_key' AND w.entity_id = k.id::text
              AND w.endpoint = '/key/service-account/generate'
        ), 0) * COALESCE(k.max_budget, 0)
        WHERE k.owner_kind = 'initiative'
    """)
    # The obsolete fixed production pool is now the projection of active internal
    # systems; normalize existing departments before direct shared-wallet allocation.
    op.execute("""
        UPDATE department AS d SET prod_budget = COALESCE((
            SELECT SUM(s.budget) FROM production_system AS s
            WHERE s.department_id = d.id AND s.funding = 'department'
              AND s.status = 'active'
        ), 0)
    """)


def downgrade() -> None:
    op.drop_column("service_key", "retired_budget")
