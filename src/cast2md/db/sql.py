"""SQL execution helper for PostgreSQL."""

from typing import Any


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
