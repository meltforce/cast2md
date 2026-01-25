"""PostgreSQL database migrations for schema changes."""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Database migrations - these run after initial schema creation
MIGRATIONS: list[dict] = [
    # Initial schema is version 10
    # Future migrations go here as the schema evolves
    {
        "version": 11,
        "description": "Rename episode status values for improved UX",
        "sql": [
            "UPDATE episode SET status = 'new' WHERE status = 'pending'",
            "UPDATE episode SET status = 'awaiting_transcript' WHERE status = 'transcript_pending'",
            "UPDATE episode SET status = 'needs_audio' WHERE status = 'transcript_unavailable'",
            "UPDATE episode SET status = 'audio_ready' WHERE status = 'downloaded'",
            # Also update the default value for the column
            "ALTER TABLE episode ALTER COLUMN status SET DEFAULT 'new'",
        ],
    },
    {
        "version": 12,
        "description": "Add pod_runs table for RunPod cost tracking",
        "sql": [
            """
            CREATE TABLE IF NOT EXISTS pod_runs (
                id SERIAL PRIMARY KEY,
                instance_id TEXT NOT NULL,
                pod_id TEXT,
                pod_name TEXT NOT NULL,
                gpu_type TEXT NOT NULL,
                gpu_price_hr REAL,
                started_at TIMESTAMP NOT NULL,
                ended_at TIMESTAMP,
                jobs_completed INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'running',
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_pod_runs_status ON pod_runs(status)",
            "CREATE INDEX IF NOT EXISTS idx_pod_runs_started_at ON pod_runs(started_at)",
        ],
    },
    {
        "version": 13,
        "description": "Add runpod_models table for customizable pod transcription models",
        "sql": [
            """
            CREATE TABLE IF NOT EXISTS runpod_models (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                backend TEXT NOT NULL DEFAULT 'whisper',
                is_enabled BOOLEAN DEFAULT TRUE,
                sort_order INTEGER DEFAULT 100,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
        ],
    },
]


def get_schema_version(conn: Any) -> int:
    """Get the current schema version from the database.

    Returns 0 if no migrations have been run.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'schema_version')"
        )
        exists = cursor.fetchone()[0]
        if not exists:
            return 0

        cursor.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
        row = cursor.fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


def set_schema_version(conn: Any, version: int) -> None:
    """Set the schema version after a successful migration."""
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO schema_version (version, applied_at) VALUES (%s, NOW())",
        (version,),
    )


def column_exists(conn: Any, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
        )
        """,
        (table, column),
    )
    return cursor.fetchone()[0]


def table_exists(conn: Any, table: str) -> bool:
    """Check if a table exists."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_name = %s
        )
        """,
        (table,),
    )
    return cursor.fetchone()[0]


def run_migrations(conn: Any) -> int:
    """Run all pending database migrations.

    Returns the number of migrations applied.
    """
    # Ensure schema_version table exists
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """
    )
    conn.commit()

    current_version = get_schema_version(conn)

    # If this is a fresh install, set version to 10
    if current_version == 0:
        set_schema_version(conn, 10)
        conn.commit()
        logger.info("Initialized database schema at version 10")
        return 0

    migrations_applied = 0

    for migration in MIGRATIONS:
        version = migration["version"]
        if version <= current_version:
            continue

        logger.info(f"Applying migration {version}: {migration['description']}")

        try:
            for sql in migration["sql"]:
                cursor.execute(sql)

            set_schema_version(conn, version)
            conn.commit()
            migrations_applied += 1
            logger.info(f"Migration {version} applied successfully")
        except Exception as e:
            conn.rollback()
            logger.error(f"Migration {version} failed: {e}")
            raise

    return migrations_applied
