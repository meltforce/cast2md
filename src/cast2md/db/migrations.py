"""Database migrations for schema changes."""

import logging
import sqlite3

logger = logging.getLogger(__name__)

MIGRATIONS = [
    {
        "version": 1,
        "description": "Add extended metadata columns to feed and episode tables",
        "sql": [
            "ALTER TABLE feed ADD COLUMN author TEXT",
            "ALTER TABLE feed ADD COLUMN link TEXT",
            "ALTER TABLE feed ADD COLUMN categories TEXT",
            "ALTER TABLE feed ADD COLUMN custom_title TEXT",
            "ALTER TABLE episode ADD COLUMN link TEXT",
            "ALTER TABLE episode ADD COLUMN author TEXT",
        ],
    },
    {
        "version": 2,
        "description": "Add distributed transcription support to job_queue",
        "sql": [
            "ALTER TABLE job_queue ADD COLUMN assigned_node_id TEXT",
            "ALTER TABLE job_queue ADD COLUMN claimed_at TEXT",
        ],
    },
    {
        "version": 3,
        "description": "Add progress tracking to job_queue",
        "sql": [
            "ALTER TABLE job_queue ADD COLUMN progress_percent INTEGER",
        ],
    },
    {
        "version": 4,
        "description": "Add transcript_model tracking to episode",
        "sql": [
            "ALTER TABLE episode ADD COLUMN transcript_model TEXT",
        ],
    },
    {
        "version": 5,
        "description": "Add transcript_source and transcript_type for external transcript support",
        "sql": [
            "ALTER TABLE episode ADD COLUMN transcript_source TEXT",
            "ALTER TABLE episode ADD COLUMN transcript_type TEXT",
            # Backfill existing completed episodes with transcript_source = 'whisper'
            "UPDATE episode SET transcript_source = 'whisper' WHERE transcript_model IS NOT NULL AND transcript_source IS NULL",
        ],
    },
    {
        "version": 6,
        "description": "Add iTunes ID and Pocket Casts UUID to feed table",
        "sql": [
            "ALTER TABLE feed ADD COLUMN itunes_id TEXT",
            "ALTER TABLE feed ADD COLUMN pocketcasts_uuid TEXT",
        ],
    },
    {
        "version": 7,
        "description": "Add segment_embeddings table for semantic search",
        "sql": [
            """
            CREATE TABLE IF NOT EXISTS segment_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id INTEGER NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
                segment_start REAL NOT NULL,
                segment_end REAL NOT NULL,
                text_hash TEXT NOT NULL,
                embedding BLOB NOT NULL,
                model_name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(episode_id, segment_start, segment_end)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_segment_embeddings_episode ON segment_embeddings(episode_id)",
        ],
    },
]


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Get the current schema version from the database.

    Returns 0 if no migrations have been run.
    """
    # Check if schema_version table exists
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    )
    if not cursor.fetchone():
        return 0

    cursor = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
    row = cursor.fetchone()
    return row[0] if row else 0


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """Set the schema version after a successful migration."""
    conn.execute(
        """
        INSERT INTO schema_version (version, applied_at)
        VALUES (?, datetime('now'))
        """,
        (version,),
    )


def ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    """Create the schema_version table if it doesn't exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    return column in columns


def run_migrations(conn: sqlite3.Connection) -> int:
    """Run all pending migrations.

    Returns the number of migrations applied.
    """
    ensure_schema_version_table(conn)
    current_version = get_schema_version(conn)

    migrations_applied = 0

    for migration in MIGRATIONS:
        version = migration["version"]
        if version <= current_version:
            continue

        logger.info(f"Applying migration {version}: {migration['description']}")

        for sql in migration["sql"]:
            try:
                # Check if this is an ALTER TABLE ADD COLUMN and skip if column exists
                if "ALTER TABLE" in sql and "ADD COLUMN" in sql:
                    parts = sql.split()
                    table_idx = parts.index("TABLE") + 1
                    column_idx = parts.index("COLUMN") + 1
                    table = parts[table_idx]
                    column = parts[column_idx]

                    if column_exists(conn, table, column):
                        logger.debug(f"Column {column} already exists in {table}, skipping")
                        continue

                conn.execute(sql)
            except sqlite3.OperationalError as e:
                # Handle case where column already exists
                if "duplicate column name" in str(e).lower():
                    logger.debug(f"Column already exists, skipping: {e}")
                    continue
                raise

        set_schema_version(conn, version)
        conn.commit()
        migrations_applied += 1
        logger.info(f"Migration {version} applied successfully")

    return migrations_applied
