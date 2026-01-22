"""SQL utilities for PostgreSQL query building."""

from typing import Any


def ph() -> str:
    """Get the parameter placeholder for PostgreSQL.

    Returns:
        '%s' for PostgreSQL.
    """
    return "%s"


def phs(n: int) -> str:
    """Get n parameter placeholders separated by commas.

    Args:
        n: Number of placeholders needed.

    Returns:
        Comma-separated placeholders (e.g., '%s, %s, %s').
    """
    return ", ".join(["%s"] * n)


def now_sql() -> str:
    """Get SQL for current timestamp.

    Returns:
        SQL expression for current timestamp.
    """
    return "NOW()"


def bool_val(val: bool) -> bool:
    """Convert boolean to database-appropriate value.

    Args:
        val: Boolean value.

    Returns:
        Boolean (PostgreSQL handles booleans natively).
    """
    return val


def returning_clause() -> str:
    """Get RETURNING clause syntax.

    Returns:
        'RETURNING *' for PostgreSQL.
    """
    return "RETURNING *"


def upsert_sql(
    table: str,
    columns: list[str],
    conflict_column: str,
    update_columns: list[str] | None = None,
) -> str:
    """Generate UPSERT SQL for PostgreSQL.

    Args:
        table: Table name.
        columns: Columns to insert.
        conflict_column: Column that defines the conflict (usually PK).
        update_columns: Columns to update on conflict (None = all non-conflict columns).

    Returns:
        UPSERT SQL statement.
    """
    if update_columns is None:
        update_columns = [c for c in columns if c != conflict_column]

    placeholders = phs(len(columns))
    insert_cols = ", ".join(columns)

    # PostgreSQL: ON CONFLICT ... DO UPDATE
    update_sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_columns)
    return f"""
        INSERT INTO {table} ({insert_cols})
        VALUES ({placeholders})
        ON CONFLICT ({conflict_column}) DO UPDATE SET {update_sets}
    """


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


def executemany(conn: Any, sql: str, params_list: list) -> Any:
    """Execute SQL with multiple parameter sets.

    Args:
        conn: Database connection.
        sql: SQL statement (use %s for parameters).
        params_list: List of parameter tuples.

    Returns:
        Cursor with results.
    """
    cursor = conn.cursor()
    cursor.executemany(sql, params_list)
    return cursor


def adapt_params(params: tuple | list) -> tuple | list:
    """Adapt parameters for the database.

    Converts types as needed for database compatibility.

    Args:
        params: Query parameters.

    Returns:
        Adapted parameters.
    """
    # Currently no adaptation needed, but this is a hook for future needs
    return params


class Query:
    """Builder for SQL queries.

    Example:
        q = Query("SELECT * FROM episodes WHERE status = %s", (status,))
        if feed_id:
            q.add(" AND feed_id = %s", (feed_id,))
        q.add(" ORDER BY published_at DESC")
        cursor = q.execute(conn)
    """

    def __init__(self, sql: str = "", params: tuple | list = ()):
        """Initialize query builder.

        Args:
            sql: Initial SQL string (use %s for parameters).
            params: Initial parameters.
        """
        self._sql = sql
        self._params = list(params)

    def add(self, sql: str, params: tuple | list = ()) -> "Query":
        """Add to the query.

        Args:
            sql: SQL to append.
            params: Parameters to add.

        Returns:
            Self for chaining.
        """
        self._sql += sql
        self._params.extend(params)
        return self

    def execute(self, conn: Any) -> Any:
        """Execute the query.

        Args:
            conn: Database connection.

        Returns:
            Cursor with results.
        """
        return execute(conn, self._sql, tuple(self._params))

    @property
    def sql(self) -> str:
        """Get the SQL string (with %s placeholders)."""
        return self._sql

    @property
    def params(self) -> tuple:
        """Get the parameters tuple."""
        return tuple(self._params)
