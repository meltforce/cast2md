"""Database connection management."""

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from cast2md.config.settings import get_settings
from cast2md.db.migrations import run_migrations
from cast2md.db.schema import get_schema

logger = logging.getLogger(__name__)

# Track if sqlite-vec extension is available
_sqlite_vec_available: bool | None = None


def _load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """Load the sqlite-vec extension for vector similarity search.

    Args:
        conn: SQLite connection to load extension into.

    Returns:
        True if extension loaded successfully, False otherwise.
    """
    global _sqlite_vec_available

    # Return cached result if we've already checked
    if _sqlite_vec_available is not None:
        if _sqlite_vec_available:
            try:
                import sqlite_vec

                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                conn.enable_load_extension(False)
            except Exception:
                pass
        return _sqlite_vec_available

    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        _sqlite_vec_available = True
        logger.info("sqlite-vec extension loaded successfully")
        return True
    except ImportError:
        _sqlite_vec_available = False
        logger.warning("sqlite-vec not installed, semantic search will be unavailable")
        return False
    except Exception as e:
        _sqlite_vec_available = False
        logger.warning(f"Failed to load sqlite-vec extension: {e}")
        return False


def is_sqlite_vec_available() -> bool:
    """Check if sqlite-vec extension is available.

    Returns:
        True if sqlite-vec can be loaded, False otherwise.
    """
    global _sqlite_vec_available
    if _sqlite_vec_available is None:
        # Do a test load to check availability
        try:
            import sqlite_vec

            test_conn = sqlite3.connect(":memory:")
            test_conn.enable_load_extension(True)
            sqlite_vec.load(test_conn)
            test_conn.close()
            _sqlite_vec_available = True
        except Exception:
            _sqlite_vec_available = False
    return _sqlite_vec_available


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

    # Set busy timeout to 30 seconds - allows waiting for write lock under contention
    conn.execute("PRAGMA busy_timeout=30000")

    # Load sqlite-vec extension for vector similarity search
    _load_sqlite_vec(conn)

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


@contextmanager
def get_db_write() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for write-heavy database operations.

    Uses BEGIN IMMEDIATE to acquire write lock upfront, preventing deadlocks
    from lock upgrades. Combined with the 30s busy_timeout, this handles
    contention gracefully.

    Yields:
        Database connection with immediate write lock.
    """
    conn = get_connection()
    try:
        # BEGIN IMMEDIATE acquires write lock at transaction start
        # This prevents deadlocks from lock upgrades (where multiple
        # transactions hold read locks and all try to upgrade to write)
        conn.execute("BEGIN IMMEDIATE")
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
