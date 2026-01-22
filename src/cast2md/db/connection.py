"""Database connection management."""

import logging
import random
import sqlite3
import time
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
def get_db_write(max_retries: int = 3) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for write-heavy database operations with retry logic.

    Uses BEGIN IMMEDIATE to acquire write lock upfront, preventing deadlocks.
    Includes retry with exponential backoff and jitter to handle contention.

    Args:
        max_retries: Maximum number of retry attempts on lock errors.

    Yields:
        Database connection with immediate write lock.
    """
    last_error = None

    for attempt in range(max_retries + 1):
        conn = get_connection()
        try:
            # BEGIN IMMEDIATE acquires write lock at transaction start
            # This prevents deadlocks from lock upgrades
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
            return
        except sqlite3.OperationalError as e:
            conn.rollback()
            conn.close()
            if "database is locked" in str(e) and attempt < max_retries:
                # Exponential backoff with jitter: 0.1-0.2s, 0.2-0.4s, 0.4-0.8s
                base_delay = 0.1 * (2**attempt)
                jitter = random.uniform(0, base_delay)
                delay = base_delay + jitter
                logger.debug(f"Database locked, retry {attempt + 1}/{max_retries} after {delay:.2f}s")
                time.sleep(delay)
                last_error = e
            else:
                raise
        except Exception:
            conn.rollback()
            conn.close()
            raise

    # Should not reach here, but just in case
    if last_error:
        raise last_error


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
