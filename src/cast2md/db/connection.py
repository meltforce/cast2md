"""Database connection management."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from cast2md.config.settings import get_settings
from cast2md.db.migrations import run_migrations
from cast2md.db.schema import get_schema


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Create a new database connection with proper settings.

    Args:
        db_path: Path to database file. Uses settings if not provided.

    Returns:
        Configured SQLite connection.
    """
    if db_path is None:
        db_path = get_settings().database_path

    conn = sqlite3.connect(str(db_path), timeout=30.0)

    # Enable WAL mode for better concurrency
    conn.execute("PRAGMA journal_mode=WAL")

    # Enable foreign key constraints
    conn.execute("PRAGMA foreign_keys=ON")

    # Set busy timeout to 5 seconds
    conn.execute("PRAGMA busy_timeout=5000")

    return conn


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for database connections.

    Yields:
        Database connection that auto-commits on success, rolls back on error.
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    """Initialize the database with schema and run migrations.

    Args:
        db_path: Path to database file. Uses settings if not provided.
    """
    settings = get_settings()
    if db_path is None:
        db_path = settings.database_path

    # Ensure directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection(db_path)
    try:
        conn.executescript(get_schema())
        conn.commit()

        # Run migrations for existing databases
        run_migrations(conn)
    finally:
        conn.close()
