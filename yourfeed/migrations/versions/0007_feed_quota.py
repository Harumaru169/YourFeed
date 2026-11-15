"""Maintain a fixed-size feed and configure concurrent external calls."""
from alembic import op

revision = "0007_feed_quota"
down_revision = "0006_llm_fallbacks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE settings RENAME COLUMN items_per_run TO feed_size;
        ALTER TABLE settings DROP COLUMN retention_hours;
        ALTER TABLE settings ADD COLUMN search_concurrency integer NOT NULL DEFAULT 5 CHECK (search_concurrency BETWEEN 1 AND 20);
        ALTER TABLE settings ADD COLUMN llm_concurrency integer NOT NULL DEFAULT 5 CHECK (llm_concurrency BETWEEN 1 AND 20);
        ALTER TABLE settings ADD COLUMN generation bigint NOT NULL DEFAULT 0;
    """)
    # Trim legacy entries, including those formerly hidden by the display period.
    from sqlalchemy import text
    from yourfeed.feed import quotas
    connection = op.get_bind()
    for genre_id, target in quotas(connection).items():
        connection.execute(text("""
            DELETE FROM timeline_entries WHERE id IN (
                SELECT id FROM timeline_entries WHERE genre_id = :genre
                ORDER BY added_at DESC, id DESC OFFSET :target)
        """), {"genre": genre_id, "target": target})


def downgrade() -> None:
    op.execute("""
        ALTER TABLE settings ADD COLUMN retention_hours integer NOT NULL DEFAULT 24;
        ALTER TABLE settings DROP COLUMN search_concurrency, DROP COLUMN llm_concurrency, DROP COLUMN generation;
        ALTER TABLE settings RENAME COLUMN feed_size TO items_per_run;
    """)
