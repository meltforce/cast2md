"""SQL execution helper for PostgreSQL."""

from typing import Any

# Type alias for a database connection (psycopg2). Lives here because every
# repository module already imports execute from this module, and this module
# imports nothing from cast2md, so it cannot take part in a cycle.
Connection = Any


def execute(conn: Any, sql: str, params: tuple | list = ()) -> Any:
    """Execute SQL with PostgreSQL cursor.

    Args:
        conn: Database connection.
        sql: SQL statement (use %s for parameters).
        params: Query parameters.

    Returns:
        Cursor with results.
    """
    cursor = conn.cursor()
    cursor.execute(sql, params)
    return cursor
