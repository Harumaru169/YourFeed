"""Providers to switch to, in order, when the main one cannot be used.

Revision ID: 0006_llm_fallbacks
Revises: 0005_retention
"""

from alembic import op

revision = "0006_llm_fallbacks"
down_revision = "0005_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE settings ADD COLUMN llm_fallbacks jsonb NOT NULL DEFAULT '[]'")


def downgrade() -> None:
    op.execute("ALTER TABLE settings DROP COLUMN llm_fallbacks")
