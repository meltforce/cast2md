"""Database configuration for PostgreSQL."""

from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseConfig(BaseSettings):
    """Database configuration with environment variable loading.

    PostgreSQL connection via the DATABASE_URL environment variable.

    Example:
        DATABASE_URL=postgresql://user:pass@host:5432/dbname
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore other env vars (e.g., whisper_model, etc.)
    )

    # DATABASE_URL for PostgreSQL connection
    database_url: Optional[str] = None

    # Connection pool settings
    pool_min_size: int = 2
    pool_max_size: int = 20

    @property
    def effective_url(self) -> str:
        """Get the effective database URL.

        Returns DATABASE_URL if set, otherwise raises an error.
        """
        if self.database_url:
            return self.database_url
        raise ValueError("DATABASE_URL environment variable is required")

    def get_postgres_dsn(self) -> str:
        """Get PostgreSQL connection string.

        Returns:
            PostgreSQL DSN for psycopg2.
        """
        url = self.effective_url
        # Normalize postgres:// to postgresql://
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return url

    def get_postgres_params(self) -> dict:
        """Parse PostgreSQL URL into connection parameters.

        Returns:
            Dict with host, port, database, user, password.
        """
        parsed = urlparse(self.effective_url)
        return {
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 5432,
            "database": parsed.path.lstrip("/") if parsed.path else "cast2md",
            "user": parsed.username or "cast2md",
            "password": parsed.password or "",
        }


# Cached config instance
_config: Optional[DatabaseConfig] = None


def get_db_config() -> DatabaseConfig:
    """Get database configuration (cached).

    Returns:
        DatabaseConfig instance.
    """
    global _config
    if _config is None:
        _config = DatabaseConfig()
    return _config


def reload_db_config() -> DatabaseConfig:
    """Reload database configuration (clears cache).

    Returns:
        Fresh DatabaseConfig instance.
    """
    global _config
    _config = DatabaseConfig()
    return _config


# SQL dialect helpers - PostgreSQL only
def get_placeholder() -> str:
    """Get the parameter placeholder for PostgreSQL.

    Returns:
        '%s' for PostgreSQL.
    """
    return "%s"


def get_placeholder_num(n: int) -> str:
    """Get numbered parameter placeholders.

    Args:
        n: Number of placeholders needed.

    Returns:
        Comma-separated placeholders (e.g., '%s, %s, %s').
    """
    return ", ".join(["%s"] * n)


def get_current_timestamp_sql() -> str:
    """Get SQL for current timestamp.

    Returns:
        SQL expression for current timestamp.
    """
    return "NOW()"


def get_autoincrement_type() -> str:
    """Get the auto-increment primary key type.

    Returns:
        SQL type for auto-increment primary key.
    """
    return "SERIAL PRIMARY KEY"


class PostgresConnectionParams:
    """PostgreSQL connection parameters for CLI commands."""

    def __init__(self, params: dict):
        self.host = params["host"]
        self.port = params["port"]
        self.database = params["database"]
        self.user = params["user"]
        self.password = params["password"]


def get_database_config() -> PostgresConnectionParams:
    """Get database connection parameters for CLI backup/restore.

    Returns:
        PostgresConnectionParams with host, port, database, user, password.
    """
    config = get_db_config()
    params = config.get_postgres_params()
    return PostgresConnectionParams(params)
