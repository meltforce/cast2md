"""Repository for database-backed settings overrides."""

from datetime import datetime

from cast2md.db.sql import Connection, execute


class SettingsRepository:
    """Repository for runtime settings overrides."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def get(self, key: str) -> str | None:
        """Get a setting value by key."""
        cursor = execute(
            self.conn,
            "SELECT value FROM settings WHERE key = %s",
            (key,),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def get_all(self) -> dict[str, str]:
        """Get all settings as a dictionary."""
        cursor = execute(self.conn, "SELECT key, value FROM settings")
        return dict(cursor.fetchall())

    def set(self, key: str, value: str) -> None:
        """Set a setting value (insert or update)."""
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
            """,
            (key, value, now),
        )
        self.conn.commit()

    def delete(self, key: str) -> bool:
        """Delete a setting (revert to default)."""
        cursor = execute(self.conn, "DELETE FROM settings WHERE key = %s", (key,))
        self.conn.commit()
        return cursor.rowcount > 0

    def set_many(self, settings: dict[str, str]) -> None:
        """Set multiple settings at once."""
        now = datetime.now().isoformat()
        for key, value in settings.items():
            execute(
                self.conn,
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
                """,
                (key, value, now),
            )
        self.conn.commit()
