"""Repository classes for database operations."""

import sqlite3
from datetime import datetime
from typing import Optional

from cast2md.db.models import Episode, EpisodeStatus, Feed


class FeedRepository:
    """Repository for Feed CRUD operations."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, url: str, title: str, description: str | None = None,
               image_url: str | None = None) -> Feed:
        """Create a new feed."""
        now = datetime.utcnow().isoformat()
        cursor = self.conn.execute(
            """
            INSERT INTO feed (url, title, description, image_url, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (url, title, description, image_url, now, now),
        )
        self.conn.commit()

        return self.get_by_id(cursor.lastrowid)

    def get_by_id(self, feed_id: int) -> Optional[Feed]:
        """Get feed by ID."""
        cursor = self.conn.execute(
            "SELECT * FROM feed WHERE id = ?",
            (feed_id,),
        )
        row = cursor.fetchone()
        return Feed.from_row(row) if row else None

    def get_by_url(self, url: str) -> Optional[Feed]:
        """Get feed by URL."""
        cursor = self.conn.execute(
            "SELECT * FROM feed WHERE url = ?",
            (url,),
        )
        row = cursor.fetchone()
        return Feed.from_row(row) if row else None

    def get_all(self) -> list[Feed]:
        """Get all feeds."""
        cursor = self.conn.execute("SELECT * FROM feed ORDER BY title")
        return [Feed.from_row(row) for row in cursor.fetchall()]

    def update_last_polled(self, feed_id: int) -> None:
        """Update the last_polled timestamp."""
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            "UPDATE feed SET last_polled = ?, updated_at = ? WHERE id = ?",
            (now, now, feed_id),
        )
        self.conn.commit()

    def delete(self, feed_id: int) -> bool:
        """Delete a feed and its episodes."""
        cursor = self.conn.execute("DELETE FROM feed WHERE id = ?", (feed_id,))
        self.conn.commit()
        return cursor.rowcount > 0


class EpisodeRepository:
    """Repository for Episode CRUD operations."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        feed_id: int,
        guid: str,
        title: str,
        audio_url: str,
        description: str | None = None,
        duration_seconds: int | None = None,
        published_at: datetime | None = None,
        transcript_url: str | None = None,
    ) -> Episode:
        """Create a new episode."""
        now = datetime.utcnow().isoformat()
        published_str = published_at.isoformat() if published_at else None

        cursor = self.conn.execute(
            """
            INSERT INTO episode (
                feed_id, guid, title, description, audio_url,
                duration_seconds, published_at, status, transcript_url,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feed_id, guid, title, description, audio_url,
                duration_seconds, published_str, EpisodeStatus.PENDING.value,
                transcript_url, now, now,
            ),
        )
        self.conn.commit()

        return self.get_by_id(cursor.lastrowid)

    def get_by_id(self, episode_id: int) -> Optional[Episode]:
        """Get episode by ID."""
        cursor = self.conn.execute(
            "SELECT * FROM episode WHERE id = ?",
            (episode_id,),
        )
        row = cursor.fetchone()
        return Episode.from_row(row) if row else None

    def get_by_guid(self, feed_id: int, guid: str) -> Optional[Episode]:
        """Get episode by feed ID and GUID."""
        cursor = self.conn.execute(
            "SELECT * FROM episode WHERE feed_id = ? AND guid = ?",
            (feed_id, guid),
        )
        row = cursor.fetchone()
        return Episode.from_row(row) if row else None

    def get_by_feed(self, feed_id: int, limit: int = 50) -> list[Episode]:
        """Get episodes for a feed, ordered by published date descending."""
        cursor = self.conn.execute(
            """
            SELECT * FROM episode
            WHERE feed_id = ?
            ORDER BY published_at DESC
            LIMIT ?
            """,
            (feed_id, limit),
        )
        return [Episode.from_row(row) for row in cursor.fetchall()]

    def get_by_status(self, status: EpisodeStatus, limit: int = 100) -> list[Episode]:
        """Get episodes by status."""
        cursor = self.conn.execute(
            """
            SELECT * FROM episode
            WHERE status = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (status.value, limit),
        )
        return [Episode.from_row(row) for row in cursor.fetchall()]

    def update_status(
        self,
        episode_id: int,
        status: EpisodeStatus,
        error_message: str | None = None,
    ) -> None:
        """Update episode status."""
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """
            UPDATE episode
            SET status = ?, error_message = ?, updated_at = ?
            WHERE id = ?
            """,
            (status.value, error_message, now, episode_id),
        )
        self.conn.commit()

    def update_audio_path(self, episode_id: int, audio_path: str) -> None:
        """Update episode audio path."""
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """
            UPDATE episode
            SET audio_path = ?, updated_at = ?
            WHERE id = ?
            """,
            (audio_path, now, episode_id),
        )
        self.conn.commit()

    def update_transcript_path(self, episode_id: int, transcript_path: str) -> None:
        """Update episode transcript path."""
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """
            UPDATE episode
            SET transcript_path = ?, updated_at = ?
            WHERE id = ?
            """,
            (transcript_path, now, episode_id),
        )
        self.conn.commit()

    def exists(self, feed_id: int, guid: str) -> bool:
        """Check if episode already exists."""
        cursor = self.conn.execute(
            "SELECT 1 FROM episode WHERE feed_id = ? AND guid = ?",
            (feed_id, guid),
        )
        return cursor.fetchone() is not None

    def count_by_status(self) -> dict[str, int]:
        """Count episodes by status."""
        cursor = self.conn.execute(
            """
            SELECT status, COUNT(*) FROM episode
            GROUP BY status
            """
        )
        return dict(cursor.fetchall())

    def delete(self, episode_id: int) -> bool:
        """Delete an episode."""
        cursor = self.conn.execute("DELETE FROM episode WHERE id = ?", (episode_id,))
        self.conn.commit()
        return cursor.rowcount > 0
