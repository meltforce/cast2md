"""Database configuration and type detection."""

from enum import Enum
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseType(Enum):
    """Supported database types."""

    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"


class DatabaseConfig(BaseSettings):
    """Database configuration with environment variable loading.

    Supports both SQLite and PostgreSQL via the DATABASE_URL environment variable.

    Examples:
        SQLite: sqlite:///data/cast2md.db or just a file path
        PostgreSQL: postgresql://user:pass@host:5432/dbname
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore other env vars (e.g., whisper_model, etc.)
    )

    # DATABASE_URL takes precedence over DATABASE_PATH
    database_url: Optional[str] = None
    database_path: str = "./data/cast2md.db"

    # Connection pool settings (PostgreSQL only)
    pool_min_size: int = 1
    pool_max_size: int = 10

    @property
    def effective_url(self) -> str:
        """Get the effective database URL.

        Returns DATABASE_URL if set, otherwise converts DATABASE_PATH to SQLite URL.
        """
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.database_path}"

    @property
    def db_type(self) -> DatabaseType:
        """Detect database type from URL."""
        url = self.effective_url
        if url.startswith("postgresql://") or url.startswith("postgres://"):
            return DatabaseType.POSTGRESQL
        return DatabaseType.SQLITE

    @property
    def is_postgresql(self) -> bool:
        """Check if using PostgreSQL."""
        return self.db_type == DatabaseType.POSTGRESQL

    @property
    def is_sqlite(self) -> bool:
        """Check if using SQLite."""
        return self.db_type == DatabaseType.SQLITE

    def get_sqlite_path(self) -> str:
        """Get SQLite file path from URL.

        Returns:
            Path to SQLite database file.

        Raises:
            ValueError: If not using SQLite.
        """
        if not self.is_sqlite:
            raise ValueError("Not using SQLite database")

        url = self.effective_url
        if url.startswith("sqlite:///"):
            return url[len("sqlite:///") :]
        return url

    def get_postgres_dsn(self) -> str:
        """Get PostgreSQL connection string.

        Returns:
            PostgreSQL DSN for psycopg2.

        Raises:
            ValueError: If not using PostgreSQL.
        """
        if not self.is_postgresql:
            raise ValueError("Not using PostgreSQL database")

        url = self.effective_url
        # Normalize postgres:// to postgresql://
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        return url

    def get_postgres_params(self) -> dict:
        """Parse PostgreSQL URL into connection parameters.

        Returns:
            Dict with host, port, database, user, password.

        Raises:
            ValueError: If not using PostgreSQL.
        """
        if not self.is_postgresql:
            raise ValueError("Not using PostgreSQL database")

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


# SQL dialect helpers
def get_placeholder() -> str:
    """Get the parameter placeholder for the current database.

    Returns:
        '?' for SQLite, '%s' for PostgreSQL.
    """
    config = get_db_config()
    return "%s" if config.is_postgresql else "?"


def get_placeholder_num(n: int) -> str:
    """Get numbered parameter placeholders.

    Args:
        n: Number of placeholders needed.

    Returns:
        Comma-separated placeholders (e.g., '?, ?, ?' or '%s, %s, %s').
    """
    placeholder = get_placeholder()
    return ", ".join([placeholder] * n)


def get_current_timestamp_sql() -> str:
    """Get SQL for current timestamp.

    Returns:
        SQL expression for current timestamp.
    """
    config = get_db_config()
    if config.is_postgresql:
        return "NOW()"
    return "datetime('now')"


def get_autoincrement_type() -> str:
    """Get the auto-increment primary key type.

    Returns:
        SQL type for auto-increment primary key.
    """
    config = get_db_config()
    if config.is_postgresql:
        return "SERIAL PRIMARY KEY"
    return "INTEGER PRIMARY KEY AUTOINCREMENT"
