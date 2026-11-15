"""Feed quotas and synchronized configuration changes."""
from sqlalchemy import text
from sqlalchemy.engine import Connection
from yourfeed.pipeline.select import allocate


def quotas(connection: Connection) -> dict:
    size = connection.execute(text("SELECT feed_size FROM settings WHERE id = 1")).scalar_one()
    rows = connection.execute(text("SELECT id, weight FROM genres ORDER BY position, created_at, id")).all()
    return allocate({row.id: row.weight for row in rows}, size)


def changed(connection: Connection) -> None:
    """Invalidate in-flight work and trim excess while holding the settings row lock."""
    connection.execute(text("UPDATE settings SET generation = generation + 1, refresh_requested_at = clock_timestamp() WHERE id = 1"))
    # Summaries prepared under an earlier configuration must not be published later.
    connection.execute(text("""
        UPDATE items SET attempted_at = NULL, skip_reason = NULL
        WHERE posted_at IS NULL AND id IN (SELECT item_id FROM summaries)
    """))
    connection.execute(text("""
        DELETE FROM summaries WHERE item_id IN (SELECT id FROM items WHERE posted_at IS NULL)
    """))
    for genre_id, target in quotas(connection).items():
        connection.execute(text("""
            DELETE FROM timeline_entries WHERE id IN (
                SELECT id FROM timeline_entries WHERE genre_id = :genre
                ORDER BY added_at DESC, id DESC OFFSET :target
            )
        """), {"genre": genre_id, "target": target})


def status(connection: Connection) -> list[dict]:
    targets = quotas(connection)
    rows = connection.execute(text("""
        SELECT g.id, g.name, count(e.id) AS current FROM genres g
        LEFT JOIN timeline_entries e ON e.genre_id = g.id
        GROUP BY g.id ORDER BY g.position, g.created_at, g.id
    """)).mappings()
    return [{"id": str(row["id"]), "name": row["name"], "current": row["current"],
             "target": targets[row["id"]]} for row in rows]
