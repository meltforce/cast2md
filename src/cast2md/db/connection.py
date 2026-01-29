"""Database connection management for PostgreSQL."""

import logging
from contextlib import contextmanager
from typing import Any, Generator, Protocol

from cast2md.db.config import get_db_config

logger = logging.getLogger(__name__)

# Connection type alias - psycopg2.connection
Connection = Any

# PostgreSQL connection pool (lazy-initialized)
_pg_pool: Any = None
_pg_pool_initialized: bool = False
_pgvector_registered_conns: set = set()


class DatabaseConnection(Protocol):
    """Protocol for database connections."""

    def execute(self, sql: str, params: tuple = ()) -> Any: ...
    def executemany(self, sql: str, params: list) -> Any: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...
    def cursor(self) -> Any: ...


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

    # Register pgvector once per connection (avoid repeated pg_type queries)
    conn_id = id(conn)
    if conn_id not in _pgvector_registered_conns:
        try:
            from pgvector.psycopg2 import register_vector

            register_vector(conn)
            _pgvector_registered_conns.add(conn_id)
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


def get_connection() -> Connection:
    """Create a new database connection.

    Returns:
        PostgreSQL connection from pool.
    """
    return _get_pg_connection()


@contextmanager
def get_db() -> Generator[Connection, None, None]:
    """Context manager for database connections.

    Yields:
        Database connection that auto-commits on success, rolls back on error.
    """
    conn = _get_pg_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _return_pg_connection(conn)


# Alias for backwards compatibility - get_db handles writes correctly
get_db_write = get_db


def init_db() -> None:
    """Initialize the database with schema and run migrations."""
    from cast2md.db.migrations import run_migrations
    from cast2md.db.schema import get_schema

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
        for statement in get_schema():
            try:
                cursor.execute(statement)
            except Exception as e:
                logger.warning(f"Schema statement failed: {e}")
                conn.rollback()
                continue

        conn.commit()

        # Run migrations
        run_migrations(conn)


def get_pool_stats() -> dict | None:
    """Get connection pool utilization stats.

    Returns:
        Dict with pool stats, or None if pool not initialized.
    """
    if _pg_pool is None:
        return None

    try:
        config = get_db_config()
        # ThreadedConnectionPool tracks used connections internally
        # _used is a dict of {conn: key} for checked-out connections
        used = len(getattr(_pg_pool, "_used", {}))
        return {
            "min_size": config.pool_min_size,
            "max_size": config.pool_max_size,
            "used": used,
            "available": config.pool_max_size - used,
        }
    except Exception:
        return None


def close_pool() -> None:
    """Close the PostgreSQL connection pool.

    Call this when shutting down the application.
    """
    global _pg_pool, _pg_pool_initialized

    if _pg_pool is not None:
        _pg_pool.closeall()
        _pg_pool = None
        _pg_pool_initialized = False
        _pgvector_registered_conns.clear()
        logger.info("PostgreSQL connection pool closed")
