"""Database connection management for SQLite and PostgreSQL."""

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Protocol, Union

from cast2md.db.config import DatabaseType, get_db_config

logger = logging.getLogger(__name__)

# Connection type alias - can be sqlite3.Connection or psycopg2.connection
Connection = Any

# Track if sqlite-vec extension is available
_sqlite_vec_available: bool | None = None

# PostgreSQL connection pool (lazy-initialized)
_pg_pool: Any = None
_pg_pool_initialized: bool = False


class DatabaseConnection(Protocol):
    """Protocol for database connections supporting both SQLite and PostgreSQL."""

    def execute(self, sql: str, params: tuple = ()) -> Any: ...
    def executemany(self, sql: str, params: list) -> Any: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...
    def cursor(self) -> Any: ...


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


def is_pgvector_available() -> bool:
    """Check if pgvector is available for PostgreSQL.

    Returns:
        True if pgvector Python bindings are installed.
    """
    try:
        import pgvector  # noqa: F401

        return True
    except ImportError:
        return False


def _get_sqlite_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Create a new SQLite connection with proper settings.

    Args:
        db_path: Path to database file. Uses config if not provided.

    Returns:
        Configured SQLite connection.
    """
    if db_path is None:
        config = get_db_config()
        db_path = Path(config.get_sqlite_path())

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


def _init_pg_pool() -> Any:
    """Initialize PostgreSQL connection pool.

    Returns:
        psycopg2 ThreadedConnectionPool.
    """
    global _pg_pool, _pg_pool_initialized

    if _pg_pool_initialized:
        return _pg_pool

    try:
        import psycopg2
        from psycopg2 import pool

        config = get_db_config()
        params = config.get_postgres_params()

        _pg_pool = pool.ThreadedConnectionPool(
            minconn=config.pool_min_size,
            maxconn=config.pool_max_size,
            host=params["host"],
            port=params["port"],
            database=params["database"],
            user=params["user"],
            password=params["password"],
        )
        _pg_pool_initialized = True
        logger.info(
            f"PostgreSQL connection pool initialized: "
            f"{config.pool_min_size}-{config.pool_max_size} connections"
        )

        # Register pgvector types if available
        _register_pgvector()

        return _pg_pool
    except ImportError:
        raise ImportError(
            "psycopg2 is required for PostgreSQL support. "
            "Install with: pip install psycopg2-binary"
        )


def _register_pgvector() -> None:
    """Register pgvector types with psycopg2."""
    try:
        from pgvector.psycopg2 import register_vector

        # Get a connection from pool to register types
        conn = _pg_pool.getconn()
        try:
            register_vector(conn)
            logger.info("pgvector types registered successfully")
        finally:
            _pg_pool.putconn(conn)
    except ImportError:
        logger.warning("pgvector not installed, vector search will be unavailable")
    except Exception as e:
        logger.warning(f"Failed to register pgvector types: {e}")


def _get_pg_connection() -> Any:
    """Get a PostgreSQL connection from the pool.

    Returns:
        psycopg2 connection from pool.
    """
    pool = _init_pg_pool()
    conn = pool.getconn()

    # Register pgvector on each connection if available
    try:
        from pgvector.psycopg2 import register_vector

        register_vector(conn)
    except (ImportError, Exception):
        pass

    return conn


def _return_pg_connection(conn: Any) -> None:
    """Return a PostgreSQL connection to the pool.

    Args:
        conn: Connection to return.
    """
    if _pg_pool is not None:
        _pg_pool.putconn(conn)


def get_connection(db_path: Path | None = None) -> Connection:
    """Create a new database connection with proper settings.

    Args:
        db_path: Path to database file (SQLite only). Uses config if not provided.

    Returns:
        Configured database connection (SQLite or PostgreSQL).
    """
    config = get_db_config()

    if config.is_postgresql:
        return _get_pg_connection()
    else:
        return _get_sqlite_connection(db_path)


@contextmanager
def get_db() -> Generator[Connection, None, None]:
    """Context manager for database connections.

    Yields:
        Database connection that auto-commits on success, rolls back on error.
    """
    config = get_db_config()

    if config.is_postgresql:
        conn = _get_pg_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            _return_pg_connection(conn)
    else:
        conn = _get_sqlite_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# Alias for backwards compatibility - get_db handles writes correctly
get_db_write = get_db


def init_db(db_path: Path | None = None) -> None:
    """Initialize the database with schema and run migrations.

    Args:
        db_path: Path to database file (SQLite only). Uses config if not provided.
    """
    from cast2md.config.settings import get_settings
    from cast2md.db.migrations import run_migrations
    from cast2md.db.schema import get_schema

    config = get_db_config()

    if config.is_postgresql:
        _init_postgresql_schema()
    else:
        # SQLite initialization
        settings = get_settings()
        if db_path is None:
            db_path = Path(config.get_sqlite_path())

        # Ensure directory exists
        db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = _get_sqlite_connection(db_path)
        try:
            conn.executescript(get_schema())
            conn.commit()

            # Run migrations for existing databases
            run_migrations(conn)
        finally:
            conn.close()


def _init_postgresql_schema() -> None:
    """Initialize PostgreSQL schema and run migrations."""
    from cast2md.db.migrations_postgres import run_postgres_migrations
    from cast2md.db.schema_postgres import get_postgres_schema

    with get_db() as conn:
        cursor = conn.cursor()

        # Enable pgvector extension
        try:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.commit()
            logger.info("pgvector extension enabled")
        except Exception as e:
            logger.warning(f"Could not enable pgvector extension: {e}")
            conn.rollback()

        # Create tables
        for statement in get_postgres_schema():
            try:
                cursor.execute(statement)
            except Exception as e:
                logger.warning(f"Schema statement failed: {e}")
                conn.rollback()
                continue

        conn.commit()

        # Run migrations
        run_postgres_migrations(conn)


def close_pool() -> None:
    """Close the PostgreSQL connection pool.

    Call this when shutting down the application.
    """
    global _pg_pool, _pg_pool_initialized

    if _pg_pool is not None:
        _pg_pool.closeall()
        _pg_pool = None
        _pg_pool_initialized = False
        logger.info("PostgreSQL connection pool closed")


def get_db_type() -> DatabaseType:
    """Get the current database type.

    Returns:
        DatabaseType.SQLITE or DatabaseType.POSTGRESQL.
    """
    return get_db_config().db_type


def is_postgresql() -> bool:
    """Check if using PostgreSQL.

    Returns:
        True if PostgreSQL is configured.
    """
    return get_db_config().is_postgresql


def is_sqlite() -> bool:
    """Check if using SQLite.

    Returns:
        True if SQLite is configured.
    """
    return get_db_config().is_sqlite
