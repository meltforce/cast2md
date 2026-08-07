"""Repository for podcast feeds."""

from datetime import datetime

from cast2md.db.models import Feed
from cast2md.db.sql import Connection, execute


class FeedRepository:
    """Repository for Feed CRUD operations."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def create(
        self,
        url: str,
        title: str,
        description: str | None = None,
        image_url: str | None = None,
        author: str | None = None,
        link: str | None = None,
        categories: str | None = None,
        itunes_id: str | None = None,
    ) -> Feed:
        """Create a new feed."""
        now = datetime.now().isoformat()

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO feed (url, title, description, image_url, author, link, categories,
                              itunes_id, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (url, title, description, image_url, author, link, categories, itunes_id, now, now),
        )
        feed_id = cursor.fetchone()[0]

        self.conn.commit()
        return self.get_by_id(feed_id)

    # Columns in the order expected by Feed.from_row
    FEED_COLUMNS = """id, url, title, description, image_url, author, link,
                      categories, custom_title, last_polled, itunes_id, pocketcasts_uuid,
                      created_at, updated_at"""

    def get_by_id(self, feed_id: int) -> Feed | None:
        """Get feed by ID."""
        cursor = execute(
            self.conn,
            f"SELECT {self.FEED_COLUMNS} FROM feed WHERE id = %s",
            (feed_id,),
        )
        row = cursor.fetchone()
        return Feed.from_row(row) if row else None

    def get_by_url(self, url: str) -> Feed | None:
        """Get feed by URL."""
        cursor = execute(
            self.conn,
            f"SELECT {self.FEED_COLUMNS} FROM feed WHERE url = %s",
            (url,),
        )
        row = cursor.fetchone()
        return Feed.from_row(row) if row else None

    def get_all(self) -> list[Feed]:
        """Get all feeds."""
        cursor = execute(self.conn, f"SELECT {self.FEED_COLUMNS} FROM feed ORDER BY title")
        return [Feed.from_row(row) for row in cursor.fetchall()]

    def update_last_polled(self, feed_id: int) -> None:
        """Update the last_polled timestamp."""
        now = datetime.now().isoformat()
        execute(
            self.conn,
            "UPDATE feed SET last_polled = %s, updated_at = %s WHERE id = %s",
            (now, now, feed_id),
        )
        self.conn.commit()

    def delete(self, feed_id: int) -> bool:
        """Delete a feed and its episodes."""
        cursor = execute(self.conn, "DELETE FROM feed WHERE id = %s", (feed_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def update(self, feed_id: int, custom_title: str | None = None) -> Feed | None:
        """Update feed custom title.

        Args:
            feed_id: Feed ID to update.
            custom_title: Custom title override (None or empty to clear).

        Returns:
            Updated feed or None if not found.
        """
        now = datetime.now().isoformat()
        # Allow setting to NULL by using empty string or None
        title_value = custom_title if custom_title else None
        execute(
            self.conn,
            """
            UPDATE feed
            SET custom_title = %s, updated_at = %s
            WHERE id = %s
            """,
            (title_value, now, feed_id),
        )
        self.conn.commit()
        return self.get_by_id(feed_id)

    def update_metadata(
        self,
        feed_id: int,
        author: str | None = None,
        link: str | None = None,
        categories: str | None = None,
    ) -> None:
        """Update feed metadata from RSS poll.

        Args:
            feed_id: Feed ID to update.
            author: Feed author.
            link: Feed website link.
            categories: JSON string of categories.
        """
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            UPDATE feed
            SET author = %s, link = %s, categories = %s, updated_at = %s
            WHERE id = %s
            """,
            (author, link, categories, now, feed_id),
        )
        self.conn.commit()

    def update_pocketcasts_uuid(self, feed_id: int, pocketcasts_uuid: str) -> None:
        """Update Pocket Casts UUID for a feed.

        Args:
            feed_id: Feed ID to update.
            pocketcasts_uuid: Pocket Casts show UUID.
        """
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            UPDATE feed
            SET pocketcasts_uuid = %s, updated_at = %s
            WHERE id = %s
            """,
            (pocketcasts_uuid, now, feed_id),
        )
        self.conn.commit()
