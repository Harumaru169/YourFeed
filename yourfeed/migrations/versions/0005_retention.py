"""The display period covers at least one update interval.

Revision ID: 0005_retention
Revises: 0004_translations
"""

from alembic import op

revision = "0005_retention"
down_revision = "0004_translations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE settings SET retention_hours = CEIL(interval_minutes / 60.0)
        WHERE retention_hours * 60 < interval_minutes
    """)


def downgrade() -> None:
    pass
