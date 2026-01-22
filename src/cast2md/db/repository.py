"""Repository classes for database operations."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from cast2md.db.models import (
    Episode,
    EpisodeStatus,
    Feed,
    Job,
    JobStatus,
    JobType,
    NodeStatus,
    TranscriberNode,
)


class FeedRepository:
    """Repository for Feed CRUD operations."""

    def __init__(self, conn: sqlite3.Connection):
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
        cursor = self.conn.execute(
            """
            INSERT INTO feed (url, title, description, image_url, author, link, categories,
                              itunes_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (url, title, description, image_url, author, link, categories, itunes_id, now, now),
        )
        self.conn.commit()

        return self.get_by_id(cursor.lastrowid)

    # Columns in the order expected by Feed.from_row
    FEED_COLUMNS = """id, url, title, description, image_url, author, link,
                      categories, custom_title, last_polled, itunes_id, pocketcasts_uuid,
                      created_at, updated_at"""

    def get_by_id(self, feed_id: int) -> Optional[Feed]:
        """Get feed by ID."""
        cursor = self.conn.execute(
            f"SELECT {self.FEED_COLUMNS} FROM feed WHERE id = ?",
            (feed_id,),
        )
        row = cursor.fetchone()
        return Feed.from_row(row) if row else None

    def get_by_url(self, url: str) -> Optional[Feed]:
        """Get feed by URL."""
        cursor = self.conn.execute(
            f"SELECT {self.FEED_COLUMNS} FROM feed WHERE url = ?",
            (url,),
        )
        row = cursor.fetchone()
        return Feed.from_row(row) if row else None

    def get_all(self) -> list[Feed]:
        """Get all feeds."""
        cursor = self.conn.execute(f"SELECT {self.FEED_COLUMNS} FROM feed ORDER BY title")
        return [Feed.from_row(row) for row in cursor.fetchall()]

    def update_last_polled(self, feed_id: int) -> None:
        """Update the last_polled timestamp."""
        now = datetime.now().isoformat()
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
        self.conn.execute(
            """
            UPDATE feed
            SET custom_title = ?, updated_at = ?
            WHERE id = ?
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
        self.conn.execute(
            """
            UPDATE feed
            SET author = ?, link = ?, categories = ?, updated_at = ?
            WHERE id = ?
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
        self.conn.execute(
            """
            UPDATE feed
            SET pocketcasts_uuid = ?, updated_at = ?
            WHERE id = ?
            """,
            (pocketcasts_uuid, now, feed_id),
        )
        self.conn.commit()


class EpisodeRepository:
    """Repository for Episode CRUD operations."""

    # Columns in the order expected by Episode.from_row
    EPISODE_COLUMNS = """id, feed_id, guid, title, description, audio_url, duration_seconds,
                         published_at, status, audio_path, transcript_path, transcript_url,
                         transcript_model, transcript_source, transcript_type,
                         pocketcasts_transcript_url, transcript_checked_at, next_transcript_retry_at,
                         transcript_failure_reason, link, author,
                         error_message, created_at, updated_at"""

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
        transcript_type: str | None = None,
        link: str | None = None,
        author: str | None = None,
    ) -> Episode:
        """Create a new episode."""
        now = datetime.now().isoformat()
        published_str = published_at.isoformat() if published_at else None

        cursor = self.conn.execute(
            """
            INSERT INTO episode (
                feed_id, guid, title, description, audio_url,
                duration_seconds, published_at, status, transcript_url,
                transcript_type, link, author, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feed_id, guid, title, description, audio_url,
                duration_seconds, published_str, EpisodeStatus.PENDING.value,
                transcript_url, transcript_type, link, author, now, now,
            ),
        )
        episode_id = cursor.lastrowid

        # Auto-index in FTS for search
        self.conn.execute(
            """
            INSERT INTO episode_fts (title, description, episode_id, feed_id)
            VALUES (?, ?, ?, ?)
            """,
            (title, description or "", episode_id, feed_id),
        )
        self.conn.commit()

        return self.get_by_id(episode_id)

    def get_by_id(self, episode_id: int) -> Optional[Episode]:
        """Get episode by ID."""
        cursor = self.conn.execute(
            f"SELECT {self.EPISODE_COLUMNS} FROM episode WHERE id = ?",
            (episode_id,),
        )
        row = cursor.fetchone()
        return Episode.from_row(row) if row else None

    def get_by_guid(self, feed_id: int, guid: str) -> Optional[Episode]:
        """Get episode by feed ID and GUID."""
        cursor = self.conn.execute(
            f"SELECT {self.EPISODE_COLUMNS} FROM episode WHERE feed_id = ? AND guid = ?",
            (feed_id, guid),
        )
        row = cursor.fetchone()
        return Episode.from_row(row) if row else None

    def get_by_feed(self, feed_id: int, limit: int = 50) -> list[Episode]:
        """Get episodes for a feed, ordered by published date descending."""
        cursor = self.conn.execute(
            f"""
            SELECT {self.EPISODE_COLUMNS} FROM episode
            WHERE feed_id = ?
            ORDER BY published_at DESC
            LIMIT ?
            """,
            (feed_id, limit),
        )
        return [Episode.from_row(row) for row in cursor.fetchall()]

    def get_by_feed_paginated(
        self, feed_id: int, limit: int = 25, offset: int = 0
    ) -> list[Episode]:
        """Get episodes with proper SQL OFFSET pagination."""
        cursor = self.conn.execute(
            f"""
            SELECT {self.EPISODE_COLUMNS} FROM episode
            WHERE feed_id = ?
            ORDER BY published_at DESC
            LIMIT ? OFFSET ?
            """,
            (feed_id, limit, offset),
        )
        return [Episode.from_row(row) for row in cursor.fetchall()]

    def get_by_status(self, status: EpisodeStatus, limit: int = 100) -> list[Episode]:
        """Get episodes by status."""
        cursor = self.conn.execute(
            f"""
            SELECT {self.EPISODE_COLUMNS} FROM episode
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
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            UPDATE episode
            SET status = ?, error_message = ?, updated_at = ?
            WHERE id = ?
            """,
            (status.value, error_message, now, episode_id),
        )
        self.conn.commit()

    def update_audio_path(self, episode_id: int, audio_path: str | None) -> None:
        """Update episode audio path.

        Args:
            episode_id: Episode ID to update.
            audio_path: Path to audio file, or None to clear.
        """
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            UPDATE episode
            SET audio_path = ?, updated_at = ?
            WHERE id = ?
            """,
            (audio_path, now, episode_id),
        )
        self.conn.commit()

    def update_audio_url(self, episode_id: int, audio_url: str) -> None:
        """Update episode audio URL.

        Used when refreshing expired/signed URLs from the feed.
        """
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            UPDATE episode
            SET audio_url = ?, updated_at = ?
            WHERE id = ?
            """,
            (audio_url, now, episode_id),
        )
        self.conn.commit()

    def update_transcript_path(self, episode_id: int, transcript_path: str) -> None:
        """Update episode transcript path."""
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            UPDATE episode
            SET transcript_path = ?, updated_at = ?
            WHERE id = ?
            """,
            (transcript_path, now, episode_id),
        )
        self.conn.commit()

    def update_transcript_path_and_model(
        self, episode_id: int, transcript_path: str, transcript_model: str
    ) -> None:
        """Update episode transcript path and model atomically.

        Sets transcript_source to 'whisper' for Whisper-transcribed episodes.
        """
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            UPDATE episode
            SET transcript_path = ?, transcript_model = ?, transcript_source = 'whisper',
                updated_at = ?
            WHERE id = ?
            """,
            (transcript_path, transcript_model, now, episode_id),
        )
        self.conn.commit()

    def update_transcript_from_download(
        self, episode_id: int, transcript_path: str, source: str
    ) -> None:
        """Update episode with downloaded transcript.

        Args:
            episode_id: Episode ID to update.
            transcript_path: Path to the transcript file.
            source: Source identifier (e.g., 'podcast2.0:vtt', 'podcast2.0:srt').
        """
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            UPDATE episode
            SET transcript_path = ?, transcript_source = ?, transcript_model = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (transcript_path, source, now, episode_id),
        )
        self.conn.commit()

    def update_pocketcasts_transcript_url(
        self, episode_id: int, pocketcasts_transcript_url: str
    ) -> None:
        """Update episode with Pocket Casts transcript URL.

        Args:
            episode_id: Episode ID to update.
            pocketcasts_transcript_url: URL to the Pocket Casts transcript.
        """
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            UPDATE episode
            SET pocketcasts_transcript_url = ?, updated_at = ?
            WHERE id = ?
            """,
            (pocketcasts_transcript_url, now, episode_id),
        )
        self.conn.commit()

    def update_transcript_check(
        self,
        episode_id: int,
        status: EpisodeStatus,
        checked_at: datetime | None,
        next_retry_at: datetime | None,
        failure_reason: str | None,
    ) -> None:
        """Update episode transcript check status and timing.

        Called after a transcript download attempt to record the result
        and schedule any retry.

        Args:
            episode_id: Episode ID to update.
            status: New status (PENDING, TRANSCRIPT_PENDING, or TRANSCRIPT_UNAVAILABLE).
            checked_at: When the check was performed (None to clear).
            next_retry_at: When to retry (for TRANSCRIPT_PENDING), or None.
            failure_reason: Type of failure (e.g., 'forbidden'), or None.
        """
        now = datetime.now().isoformat()
        checked_str = checked_at.isoformat() if checked_at else None
        retry_str = next_retry_at.isoformat() if next_retry_at else None
        self.conn.execute(
            """
            UPDATE episode
            SET status = ?, transcript_checked_at = ?, next_transcript_retry_at = ?,
                transcript_failure_reason = ?, updated_at = ?
            WHERE id = ?
            """,
            (status.value, checked_str, retry_str, failure_reason, now, episode_id),
        )
        self.conn.commit()

    def get_episodes_for_transcript_retry(self) -> list[Episode]:
        """Get episodes that are due for transcript retry.

        Returns episodes with:
        - status = 'transcript_pending'
        - next_transcript_retry_at <= now

        Returns:
            List of episodes ready for retry.
        """
        now = datetime.now().isoformat()
        cursor = self.conn.execute(
            f"""
            SELECT {self.EPISODE_COLUMNS} FROM episode
            WHERE status = ?
              AND next_transcript_retry_at IS NOT NULL
              AND next_transcript_retry_at <= ?
            ORDER BY next_transcript_retry_at ASC
            """,
            (EpisodeStatus.TRANSCRIPT_PENDING.value, now),
        )
        return [Episode.from_row(row) for row in cursor.fetchall()]

    def get_status_counts_for_feed(self, feed_id: int) -> dict[str, int]:
        """Get episode counts by status for a feed.

        Returns:
            Dict mapping status values to counts.
        """
        cursor = self.conn.execute(
            """
            SELECT status, COUNT(*) FROM episode
            WHERE feed_id = ?
            GROUP BY status
            """,
            (feed_id,),
        )
        return dict(cursor.fetchall())

    def get_retranscribable_episodes(
        self, feed_id: int, current_model: str
    ) -> list[Episode]:
        """Get completed episodes where transcript_model differs from current model.

        Args:
            feed_id: Feed ID to filter by.
            current_model: The current whisper model to compare against.

        Returns:
            List of episodes that can be re-transcribed.
        """
        cursor = self.conn.execute(
            f"""
            SELECT {self.EPISODE_COLUMNS} FROM episode
            WHERE feed_id = ?
              AND status = ?
              AND (transcript_model IS NULL OR transcript_model != ?)
            ORDER BY published_at DESC
            """,
            (feed_id, EpisodeStatus.COMPLETED.value, current_model),
        )
        return [Episode.from_row(row) for row in cursor.fetchall()]

    def count_retranscribable_episodes(self, feed_id: int, current_model: str) -> int:
        """Count completed episodes where transcript_model differs from current model.

        Args:
            feed_id: Feed ID to filter by.
            current_model: The current whisper model to compare against.

        Returns:
            Count of episodes that can be re-transcribed.
        """
        cursor = self.conn.execute(
            """
            SELECT COUNT(*) FROM episode
            WHERE feed_id = ?
              AND status = ?
              AND (transcript_model IS NULL OR transcript_model != ?)
            """,
            (feed_id, EpisodeStatus.COMPLETED.value, current_model),
        )
        return cursor.fetchone()[0]

    def update_paths_for_feed_rename(
        self, feed_id: int, old_dir_name: str, new_dir_name: str
    ) -> int:
        """Update all episode paths when a feed directory is renamed.

        Replaces the old directory name with the new one in audio_path and
        transcript_path for all episodes of the given feed.

        Args:
            feed_id: The feed ID whose episodes to update.
            old_dir_name: The old sanitized directory name.
            new_dir_name: The new sanitized directory name.

        Returns:
            Number of episodes updated.
        """
        now = datetime.now().isoformat()

        # Update audio_path
        cursor = self.conn.execute(
            """
            UPDATE episode
            SET audio_path = REPLACE(audio_path, ?, ?),
                updated_at = ?
            WHERE feed_id = ? AND audio_path IS NOT NULL AND audio_path LIKE ?
            """,
            (
                f"/{old_dir_name}/",
                f"/{new_dir_name}/",
                now,
                feed_id,
                f"%/{old_dir_name}/%",
            ),
        )
        audio_updated = cursor.rowcount

        # Update transcript_path
        cursor = self.conn.execute(
            """
            UPDATE episode
            SET transcript_path = REPLACE(transcript_path, ?, ?),
                updated_at = ?
            WHERE feed_id = ? AND transcript_path IS NOT NULL AND transcript_path LIKE ?
            """,
            (
                f"/{old_dir_name}/",
                f"/{new_dir_name}/",
                now,
                feed_id,
                f"%/{old_dir_name}/%",
            ),
        )

        self.conn.commit()
        return max(audio_updated, cursor.rowcount)

    def exists(self, feed_id: int, guid: str) -> bool:
        """Check if episode already exists."""
        cursor = self.conn.execute(
            "SELECT 1 FROM episode WHERE feed_id = ? AND guid = ?",
            (feed_id, guid),
        )
        return cursor.fetchone() is not None

    def count_by_feed(self, feed_id: int) -> int:
        """Count total episodes for a feed."""
        cursor = self.conn.execute(
            "SELECT COUNT(*) FROM episode WHERE feed_id = ?",
            (feed_id,),
        )
        return cursor.fetchone()[0]

    def get_transcript_source_stats(self, feed_id: int) -> dict:
        """Get statistics about transcript sources for a feed.

        Returns:
            Dict with counts for each transcript source type:
            - podcast20: Episodes with transcript_url (Podcast 2.0 tags)
            - pocketcasts: Episodes with pocketcasts_transcript_url (no Podcast 2.0)
            - whisper_only: Episodes with neither (need Whisper transcription)
        """
        # Count episodes with Podcast 2.0 transcript URLs
        cursor = self.conn.execute(
            "SELECT COUNT(*) FROM episode WHERE feed_id = ? AND transcript_url IS NOT NULL",
            (feed_id,),
        )
        podcast20_count = cursor.fetchone()[0]

        # Count episodes with Pocket Casts transcripts (but no Podcast 2.0)
        cursor = self.conn.execute(
            """SELECT COUNT(*) FROM episode
               WHERE feed_id = ?
                 AND transcript_url IS NULL
                 AND pocketcasts_transcript_url IS NOT NULL""",
            (feed_id,),
        )
        pocketcasts_count = cursor.fetchone()[0]

        # Count episodes with neither
        cursor = self.conn.execute(
            """SELECT COUNT(*) FROM episode
               WHERE feed_id = ?
                 AND transcript_url IS NULL
                 AND pocketcasts_transcript_url IS NULL""",
            (feed_id,),
        )
        whisper_only_count = cursor.fetchone()[0]

        return {
            "podcast20": podcast20_count,
            "pocketcasts": pocketcasts_count,
            "whisper_only": whisper_only_count,
        }

    def search_by_feed(
        self,
        feed_id: int,
        query: str | None = None,
        status: EpisodeStatus | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[Episode], int]:
        """Search episodes by title/description with optional status filter.

        Uses FTS5 full-text search when query is provided for word-boundary matching.

        Returns: (episodes, total_count)
        """
        # Use FTS search when query is provided (word-boundary matching)
        if query:
            episode_ids, fts_total = self.search_episodes_fts(
                query, feed_id=feed_id, limit=limit, offset=offset
            )

            if not episode_ids:
                return [], 0

            # Fetch full episode data for matching IDs
            # Preserve FTS ranking order
            placeholders = ",".join("?" for _ in episode_ids)
            id_order = ",".join(str(eid) for eid in episode_ids)

            # Build query with optional status filter
            if status:
                cursor = self.conn.execute(
                    f"""
                    SELECT {self.EPISODE_COLUMNS} FROM episode
                    WHERE id IN ({placeholders}) AND status = ?
                    ORDER BY CASE id {' '.join(f'WHEN {eid} THEN {i}' for i, eid in enumerate(episode_ids))} END
                    """,
                    (*episode_ids, status.value),
                )
                # Recount with status filter
                count_cursor = self.conn.execute(
                    f"""
                    SELECT COUNT(*) FROM episode
                    WHERE id IN (
                        SELECT episode_id FROM episode_fts
                        WHERE episode_fts MATCH ? AND feed_id = ?
                    ) AND status = ?
                    """,
                    (" ".join(f"{w}*" for w in query.split() if w), feed_id, status.value),
                )
                total = count_cursor.fetchone()[0]
            else:
                cursor = self.conn.execute(
                    f"""
                    SELECT {self.EPISODE_COLUMNS} FROM episode
                    WHERE id IN ({placeholders})
                    ORDER BY CASE id {' '.join(f'WHEN {eid} THEN {i}' for i, eid in enumerate(episode_ids))} END
                    """,
                    episode_ids,
                )
                total = fts_total

            episodes = [Episode.from_row(row) for row in cursor.fetchall()]
            return episodes, total

        # No query - use simple SQL filtering
        conditions = ["feed_id = ?"]
        params: list = [feed_id]

        if status:
            conditions.append("status = ?")
            params.append(status.value)

        where_clause = " AND ".join(conditions)

        # Get total count
        count_cursor = self.conn.execute(
            f"SELECT COUNT(*) FROM episode WHERE {where_clause}",
            params,
        )
        total = count_cursor.fetchone()[0]

        # Get paginated results
        params.extend([limit, offset])
        cursor = self.conn.execute(
            f"""
            SELECT {self.EPISODE_COLUMNS} FROM episode
            WHERE {where_clause}
            ORDER BY published_at DESC
            LIMIT ? OFFSET ?
            """,
            params,
        )
        episodes = [Episode.from_row(row) for row in cursor.fetchall()]

        return episodes, total

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
        # Also remove from FTS index
        self.conn.execute("DELETE FROM episode_fts WHERE episode_id = ?", (episode_id,))
        cursor = self.conn.execute("DELETE FROM episode WHERE id = ?", (episode_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    # --- FTS indexing methods ---

    def index_episode(
        self,
        episode_id: int,
        title: str,
        description: str | None,
        feed_id: int,
    ) -> None:
        """Add or update an episode in the FTS index."""
        # Delete existing entry if any
        self.conn.execute("DELETE FROM episode_fts WHERE episode_id = ?", (episode_id,))
        # Insert new entry
        self.conn.execute(
            """
            INSERT INTO episode_fts (title, description, episode_id, feed_id)
            VALUES (?, ?, ?, ?)
            """,
            (title, description or "", episode_id, feed_id),
        )
        self.conn.commit()

    def reindex_all_episodes(self) -> int:
        """Rebuild the entire episode FTS index from the episode table.

        Returns:
            Number of episodes indexed.
        """
        # Clear existing FTS data
        self.conn.execute("DELETE FROM episode_fts")

        # Index all episodes
        cursor = self.conn.execute(
            "SELECT id, feed_id, title, description FROM episode"
        )
        count = 0
        for row in cursor.fetchall():
            episode_id, feed_id, title, description = row
            self.conn.execute(
                """
                INSERT INTO episode_fts (title, description, episode_id, feed_id)
                VALUES (?, ?, ?, ?)
                """,
                (title, description or "", episode_id, feed_id),
            )
            count += 1

        self.conn.commit()
        return count

    def search_episodes_fts(
        self,
        query: str,
        feed_id: int | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[int], int]:
        """Search episodes using FTS5 full-text search.

        Args:
            query: Search query (will be converted to FTS5 syntax).
            feed_id: Optional feed ID to filter results.
            limit: Maximum results per page.
            offset: Pagination offset.

        Returns:
            (list of episode IDs, total count)
        """
        # Use exact word matching for episode title/description search
        # (no prefix matching - "ai" should not match "air" or "airline")
        fts_query = " ".join(word for word in query.split() if word)

        if feed_id is not None:
            # Count total matches for this feed
            count_cursor = self.conn.execute(
                """
                SELECT COUNT(*) FROM episode_fts
                WHERE episode_fts MATCH ? AND feed_id = ?
                """,
                (fts_query, feed_id),
            )
            total = count_cursor.fetchone()[0]

            # Get paginated episode IDs
            cursor = self.conn.execute(
                """
                SELECT episode_id FROM episode_fts
                WHERE episode_fts MATCH ? AND feed_id = ?
                ORDER BY rank
                LIMIT ? OFFSET ?
                """,
                (fts_query, feed_id, limit, offset),
            )
        else:
            # Count total matches across all feeds
            count_cursor = self.conn.execute(
                "SELECT COUNT(*) FROM episode_fts WHERE episode_fts MATCH ?",
                (fts_query,),
            )
            total = count_cursor.fetchone()[0]

            # Get paginated episode IDs
            cursor = self.conn.execute(
                """
                SELECT episode_id FROM episode_fts
                WHERE episode_fts MATCH ?
                ORDER BY rank
                LIMIT ? OFFSET ?
                """,
                (fts_query, limit, offset),
            )

        episode_ids = [row[0] for row in cursor.fetchall()]
        return episode_ids, total

    def get_recent_episodes(
        self,
        days: int = 7,
        limit: int = 50,
    ) -> list[tuple[Episode, str]]:
        """Get recently published episodes across all feeds.

        Args:
            days: Number of days to look back (default: 7).
            limit: Maximum episodes to return (default: 50).

        Returns:
            List of tuples (Episode, feed_title) sorted by published_at descending.
        """
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        # Prefix episode columns with table alias
        ep_cols = ", ".join(f"e.{c.strip()}" for c in self.EPISODE_COLUMNS.split(","))
        cursor = self.conn.execute(
            f"""
            SELECT {ep_cols}, COALESCE(f.custom_title, f.title) as feed_title
            FROM episode e
            JOIN feed f ON e.feed_id = f.id
            WHERE e.published_at >= ?
            ORDER BY e.published_at DESC
            LIMIT ?
            """,
            (cutoff, limit),
        )
        results = []
        for row in cursor.fetchall():
            # Episode columns are all but the last one (feed_title)
            episode = Episode.from_row(row[:-1])
            feed_title = row[-1]
            results.append((episode, feed_title))
        return results

    def search_episodes_fts_full(
        self,
        query: str,
        feed_id: int | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[Episode], int]:
        """Search episodes using FTS5 and return full Episode objects.

        Args:
            query: Search query (will be converted to FTS5 syntax).
            feed_id: Optional feed ID to filter results.
            limit: Maximum results per page.
            offset: Pagination offset.

        Returns:
            (list of Episode objects, total count)
        """
        episode_ids, total = self.search_episodes_fts(
            query=query,
            feed_id=feed_id,
            limit=limit,
            offset=offset,
        )

        if not episode_ids:
            return [], total

        # Fetch full Episode objects, preserving FTS ranking order
        placeholders = ",".join("?" for _ in episode_ids)
        id_order = " ".join(f"WHEN {eid} THEN {i}" for i, eid in enumerate(episode_ids))

        cursor = self.conn.execute(
            f"""
            SELECT {self.EPISODE_COLUMNS} FROM episode
            WHERE id IN ({placeholders})
            ORDER BY CASE id {id_order} END
            """,
            episode_ids,
        )

        episodes = [Episode.from_row(row) for row in cursor.fetchall()]
        return episodes, total


class JobRepository:
    """Repository for Job queue operations."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        episode_id: int,
        job_type: JobType,
        priority: int = 10,
        max_attempts: int = 3,
    ) -> Job:
        """Create a new job in the queue."""
        now = datetime.now().isoformat()

        cursor = self.conn.execute(
            """
            INSERT INTO job_queue (
                episode_id, job_type, priority, status, attempts,
                max_attempts, scheduled_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                episode_id, job_type.value, priority, JobStatus.QUEUED.value,
                0, max_attempts, now, now,
            ),
        )
        self.conn.commit()

        return self.get_by_id(cursor.lastrowid)

    def get_by_id(self, job_id: int) -> Optional[Job]:
        """Get job by ID."""
        cursor = self.conn.execute(
            "SELECT * FROM job_queue WHERE id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        return Job.from_row(row) if row else None

    def get_next_job(self, job_type: JobType, local_only: bool = False) -> Optional[Job]:
        """Get the next queued job of given type, ordered by priority.

        Also respects next_retry_at for failed jobs being retried.

        Args:
            job_type: Type of job to get.
            local_only: If True, only return jobs not assigned to a node.
        """
        now = datetime.now().isoformat()
        if local_only:
            cursor = self.conn.execute(
                """
                SELECT * FROM job_queue
                WHERE job_type = ?
                  AND status = ?
                  AND assigned_node_id IS NULL
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY priority ASC, scheduled_at ASC
                LIMIT 1
                """,
                (job_type.value, JobStatus.QUEUED.value, now),
            )
        else:
            cursor = self.conn.execute(
                """
                SELECT * FROM job_queue
                WHERE job_type = ?
                  AND status = ?
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY priority ASC, scheduled_at ASC
                LIMIT 1
                """,
                (job_type.value, JobStatus.QUEUED.value, now),
            )
        row = cursor.fetchone()
        return Job.from_row(row) if row else None

    def get_next_unclaimed_job(self, job_type: JobType) -> Optional[Job]:
        """Get the next queued job that hasn't been claimed by any node.

        Used by distributed transcription nodes to claim work.
        """
        now = datetime.now().isoformat()
        cursor = self.conn.execute(
            """
            SELECT * FROM job_queue
            WHERE job_type = ?
              AND status = ?
              AND assigned_node_id IS NULL
              AND (next_retry_at IS NULL OR next_retry_at <= ?)
            ORDER BY priority ASC, scheduled_at ASC
            LIMIT 1
            """,
            (job_type.value, JobStatus.QUEUED.value, now),
        )
        row = cursor.fetchone()
        return Job.from_row(row) if row else None

    def claim_job(self, job_id: int, node_id: str) -> None:
        """Claim a job for a specific node."""
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            UPDATE job_queue
            SET assigned_node_id = ?, claimed_at = ?, status = ?, started_at = ?,
                attempts = attempts + 1, progress_percent = 0
            WHERE id = ?
            """,
            (node_id, now, JobStatus.RUNNING.value, now, job_id),
        )
        self.conn.commit()

    def unclaim_job(self, job_id: int) -> None:
        """Remove node assignment from a job (for retries or failed nodes)."""
        self.conn.execute(
            """
            UPDATE job_queue
            SET assigned_node_id = NULL, claimed_at = NULL
            WHERE id = ?
            """,
            (job_id,),
        )
        self.conn.commit()

    def get_jobs_by_node(self, node_id: str) -> list[Job]:
        """Get all jobs assigned to a specific node."""
        cursor = self.conn.execute(
            """
            SELECT * FROM job_queue
            WHERE assigned_node_id = ?
            ORDER BY claimed_at DESC
            """,
            (node_id,),
        )
        return [Job.from_row(row) for row in cursor.fetchall()]

    def reclaim_stale_jobs(self, timeout_hours: int = 2) -> tuple[int, int]:
        """Reclaim jobs that have been running too long on a node.

        Jobs that have been running longer than timeout_hours on a node
        are either reset to queued state (if retries remain) or marked as
        permanently failed (if max attempts exceeded).

        Returns:
            Tuple of (jobs_requeued, jobs_failed).
        """
        threshold = (datetime.now() - timedelta(hours=timeout_hours)).isoformat()
        now = datetime.now().isoformat()

        # First, fail jobs that have exceeded max attempts
        # Use started_at (not claimed_at) so reclaim cycles don't reset the timeout
        cursor = self.conn.execute(
            """
            UPDATE job_queue
            SET status = ?, error_message = 'Max attempts exceeded (job timed out repeatedly)',
                completed_at = ?, assigned_node_id = NULL, claimed_at = NULL
            WHERE status = ?
              AND assigned_node_id IS NOT NULL
              AND started_at < ?
              AND attempts >= max_attempts
            """,
            (JobStatus.FAILED.value, now, JobStatus.RUNNING.value, threshold),
        )
        jobs_failed = cursor.rowcount

        # Then, requeue jobs that still have retries remaining
        cursor = self.conn.execute(
            """
            UPDATE job_queue
            SET status = ?, assigned_node_id = NULL, claimed_at = NULL, started_at = NULL
            WHERE status = ?
              AND assigned_node_id IS NOT NULL
              AND started_at < ?
              AND attempts < max_attempts
            """,
            (JobStatus.QUEUED.value, JobStatus.RUNNING.value, threshold),
        )
        jobs_requeued = cursor.rowcount

        self.conn.commit()
        return jobs_requeued, jobs_failed

    def get_running_jobs(self, job_type: JobType) -> list[Job]:
        """Get all running jobs of given type."""
        cursor = self.conn.execute(
            """
            SELECT * FROM job_queue
            WHERE job_type = ? AND status = ?
            ORDER BY started_at ASC
            """,
            (job_type.value, JobStatus.RUNNING.value),
        )
        return [Job.from_row(row) for row in cursor.fetchall()]

    def get_queued_jobs(self, job_type: JobType | None = None, limit: int = 100) -> list[Job]:
        """Get queued jobs ready to run (excludes jobs waiting for retry)."""
        now = datetime.now().isoformat()
        if job_type:
            cursor = self.conn.execute(
                """
                SELECT * FROM job_queue
                WHERE job_type = ? AND status = ?
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY priority ASC, scheduled_at ASC
                LIMIT ?
                """,
                (job_type.value, JobStatus.QUEUED.value, now, limit),
            )
        else:
            cursor = self.conn.execute(
                """
                SELECT * FROM job_queue
                WHERE status = ?
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY priority ASC, scheduled_at ASC
                LIMIT ?
                """,
                (JobStatus.QUEUED.value, now, limit),
            )
        return [Job.from_row(row) for row in cursor.fetchall()]

    def get_by_episode(self, episode_id: int) -> list[Job]:
        """Get all jobs for an episode."""
        cursor = self.conn.execute(
            """
            SELECT * FROM job_queue
            WHERE episode_id = ?
            ORDER BY created_at DESC
            """,
            (episode_id,),
        )
        return [Job.from_row(row) for row in cursor.fetchall()]

    def has_pending_job(self, episode_id: int, job_type: JobType) -> bool:
        """Check if episode has a pending or running job of given type."""
        cursor = self.conn.execute(
            """
            SELECT 1 FROM job_queue
            WHERE episode_id = ? AND job_type = ? AND status IN (?, ?)
            """,
            (episode_id, job_type.value, JobStatus.QUEUED.value, JobStatus.RUNNING.value),
        )
        return cursor.fetchone() is not None

    def mark_running(self, job_id: int, node_id: str = "local") -> None:
        """Mark a job as running.

        Args:
            job_id: The job ID to mark as running.
            node_id: The node ID processing this job (default: "local" for local workers).
        """
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            UPDATE job_queue
            SET status = ?, started_at = ?, attempts = attempts + 1,
                progress_percent = 0, assigned_node_id = ?, claimed_at = ?
            WHERE id = ?
            """,
            (JobStatus.RUNNING.value, now, node_id, now, job_id),
        )
        self.conn.commit()

    def mark_completed(self, job_id: int) -> None:
        """Mark a job as completed."""
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            UPDATE job_queue
            SET status = ?, completed_at = ?, progress_percent = 100
            WHERE id = ?
            """,
            (JobStatus.COMPLETED.value, now, job_id),
        )
        self.conn.commit()

    def update_progress(self, job_id: int, progress_percent: int) -> None:
        """Update job progress percentage.

        Args:
            job_id: Job ID to update.
            progress_percent: Progress percentage (0-100).
        """
        # Clamp to valid range
        progress_percent = max(0, min(100, progress_percent))
        self.conn.execute(
            """
            UPDATE job_queue
            SET progress_percent = ?
            WHERE id = ?
            """,
            (progress_percent, job_id),
        )
        self.conn.commit()

    def reset_running_jobs(self) -> tuple[int, int]:
        """Reset all running jobs back to queued status or fail if max attempts exceeded.

        Called on server startup to handle jobs orphaned from previous run.
        Also resets the episode status back to downloaded/pending as appropriate,
        or to failed if max attempts exceeded.

        Returns:
            Tuple of (jobs_requeued, jobs_failed).
        """
        from cast2md.db.models import EpisodeStatus

        now = datetime.now().isoformat()

        # Find all running jobs with their attempt counts
        cursor = self.conn.execute(
            """
            SELECT id, episode_id, job_type, attempts, max_attempts FROM job_queue
            WHERE status = ?
            """,
            (JobStatus.RUNNING.value,),
        )
        running_jobs = cursor.fetchall()

        if not running_jobs:
            return 0, 0

        jobs_to_requeue = []
        jobs_to_fail = []

        for job_id, episode_id, job_type, attempts, max_attempts in running_jobs:
            if attempts >= max_attempts:
                jobs_to_fail.append((job_id, episode_id, job_type))
            else:
                jobs_to_requeue.append((job_id, episode_id, job_type))

        # Fail jobs that have exceeded max attempts
        if jobs_to_fail:
            job_ids = [j[0] for j in jobs_to_fail]
            placeholders = ",".join("?" for _ in job_ids)
            self.conn.execute(
                f"""
                UPDATE job_queue
                SET status = ?, error_message = 'Max attempts exceeded (orphaned on restart)',
                    completed_at = ?, assigned_node_id = NULL, claimed_at = NULL,
                    progress_percent = NULL
                WHERE id IN ({placeholders})
                """,
                [JobStatus.FAILED.value, now] + job_ids,
            )

            # Set episode status to failed
            for job_id, episode_id, job_type in jobs_to_fail:
                self.conn.execute(
                    "UPDATE episode SET status = ?, error_message = ? WHERE id = ?",
                    (EpisodeStatus.FAILED.value, "Max attempts exceeded", episode_id),
                )

        # Requeue jobs that still have retries
        if jobs_to_requeue:
            job_ids = [j[0] for j in jobs_to_requeue]
            placeholders = ",".join("?" for _ in job_ids)
            self.conn.execute(
                f"""
                UPDATE job_queue
                SET status = ?, started_at = NULL, assigned_node_id = NULL,
                    claimed_at = NULL, progress_percent = NULL
                WHERE id IN ({placeholders})
                """,
                [JobStatus.QUEUED.value] + job_ids,
            )

            # Reset episode statuses
            for job_id, episode_id, job_type in jobs_to_requeue:
                if job_type == JobType.DOWNLOAD.value:
                    self.conn.execute(
                        "UPDATE episode SET status = ? WHERE id = ?",
                        (EpisodeStatus.PENDING.value, episode_id),
                    )
                elif job_type == JobType.TRANSCRIBE.value:
                    self.conn.execute(
                        "UPDATE episode SET status = ? WHERE id = ?",
                        (EpisodeStatus.DOWNLOADED.value, episode_id),
                    )
                elif job_type == JobType.TRANSCRIPT_DOWNLOAD.value:
                    # Transcript download jobs don't change episode status during processing
                    # Episode stays in PENDING until transcript is found or user queues download
                    pass

        self.conn.commit()
        return len(jobs_to_requeue), len(jobs_to_fail)

    def mark_failed(self, job_id: int, error_message: str, retry: bool = True) -> None:
        """Mark a job as failed, optionally scheduling a retry."""
        now = datetime.now()

        # Get current job to check attempts
        job = self.get_by_id(job_id)
        if not job:
            return

        if retry and job.attempts < job.max_attempts:
            # Schedule retry with exponential backoff (5min, 25min, 125min)
            backoff_minutes = 5 ** job.attempts
            next_retry = now + timedelta(minutes=backoff_minutes)

            self.conn.execute(
                """
                UPDATE job_queue
                SET status = ?, error_message = ?, next_retry_at = ?
                WHERE id = ?
                """,
                (JobStatus.QUEUED.value, error_message, next_retry.isoformat(), job_id),
            )
        else:
            # Max attempts reached, mark as failed
            self.conn.execute(
                """
                UPDATE job_queue
                SET status = ?, error_message = ?, completed_at = ?
                WHERE id = ?
                """,
                (JobStatus.FAILED.value, error_message, now.isoformat(), job_id),
            )
        self.conn.commit()

    def count_by_status(self, job_type: JobType | None = None) -> dict[str, int]:
        """Count jobs by status."""
        if job_type:
            cursor = self.conn.execute(
                """
                SELECT status, COUNT(*) FROM job_queue
                WHERE job_type = ?
                GROUP BY status
                """,
                (job_type.value,),
            )
        else:
            cursor = self.conn.execute(
                """
                SELECT status, COUNT(*) FROM job_queue
                GROUP BY status
                """
            )
        return dict(cursor.fetchall())

    def delete(self, job_id: int) -> bool:
        """Delete a job."""
        cursor = self.conn.execute("DELETE FROM job_queue WHERE id = ?", (job_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def cancel_queued(self, job_id: int) -> bool:
        """Cancel a queued job (only if not running)."""
        cursor = self.conn.execute(
            """
            DELETE FROM job_queue
            WHERE id = ? AND status = ?
            """,
            (job_id, JobStatus.QUEUED.value),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def cleanup_completed(self, older_than_days: int = 7) -> int:
        """Delete completed/failed jobs older than N days."""
        cutoff = (datetime.now() - timedelta(days=older_than_days)).isoformat()

        cursor = self.conn.execute(
            """
            DELETE FROM job_queue
            WHERE status IN (?, ?) AND completed_at < ?
            """,
            (JobStatus.COMPLETED.value, JobStatus.FAILED.value, cutoff),
        )
        self.conn.commit()
        return cursor.rowcount

    def get_stuck_jobs(self, threshold_hours: int = 2) -> list[Job]:
        """Get jobs that have been running longer than threshold.

        Args:
            threshold_hours: Hours after which a running job is considered stuck.

        Returns:
            List of stuck jobs.
        """
        threshold = (datetime.now() - timedelta(hours=threshold_hours)).isoformat()
        cursor = self.conn.execute(
            """
            SELECT * FROM job_queue
            WHERE status = ?
            AND started_at < ?
            ORDER BY started_at ASC
            """,
            (JobStatus.RUNNING.value, threshold),
        )
        return [Job.from_row(row) for row in cursor.fetchall()]

    def force_reset(self, job_id: int) -> bool:
        """Force reset a running/stuck job back to queued state.

        Clears started_at, assigned_node_id, claimed_at and resets status to queued.

        Args:
            job_id: Job ID to reset.

        Returns:
            True if job was reset, False if not found or not in running state.
        """
        cursor = self.conn.execute(
            """
            UPDATE job_queue
            SET status = ?, started_at = NULL, error_message = NULL,
                assigned_node_id = NULL, claimed_at = NULL, progress_percent = 0
            WHERE id = ? AND status = ?
            """,
            (JobStatus.QUEUED.value, job_id, JobStatus.RUNNING.value),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_all_jobs(
        self,
        status: JobStatus | None = None,
        job_type: JobType | None = None,
        limit: int = 100,
        include_stuck: bool = False,
        stuck_threshold_hours: int = 2,
    ) -> list[Job]:
        """Get all jobs with optional filters.

        Args:
            status: Filter by job status.
            job_type: Filter by job type.
            limit: Maximum number of jobs to return.
            include_stuck: If True and status is None, includes stuck indicator.
            stuck_threshold_hours: Hours after which running job is stuck.

        Returns:
            List of jobs ordered by priority, then scheduled time.
        """
        conditions = []
        params = []

        if status:
            conditions.append("status = ?")
            params.append(status.value)

        if job_type:
            conditions.append("job_type = ?")
            params.append(job_type.value)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        params.append(limit)
        cursor = self.conn.execute(
            f"""
            SELECT * FROM job_queue
            {where_clause}
            ORDER BY
                CASE status
                    WHEN 'running' THEN 0
                    WHEN 'queued' THEN 1
                    WHEN 'failed' THEN 2
                    WHEN 'completed' THEN 3
                END,
                priority ASC,
                scheduled_at ASC
            LIMIT ?
            """,
            params,
        )
        return [Job.from_row(row) for row in cursor.fetchall()]

    def get_failed_jobs(self, limit: int = 100) -> list[Job]:
        """Get all failed jobs.

        Args:
            limit: Maximum number of jobs to return.

        Returns:
            List of failed jobs.
        """
        cursor = self.conn.execute(
            """
            SELECT * FROM job_queue
            WHERE status = ?
            ORDER BY completed_at DESC
            LIMIT ?
            """,
            (JobStatus.FAILED.value, limit),
        )
        return [Job.from_row(row) for row in cursor.fetchall()]

    def retry_failed_job(self, job_id: int) -> bool:
        """Retry a failed job by resetting it to queued state.

        Args:
            job_id: Job ID to retry.

        Returns:
            True if job was reset, False if not found or not failed.
        """
        cursor = self.conn.execute(
            """
            UPDATE job_queue
            SET status = ?, attempts = 0, error_message = NULL,
                next_retry_at = NULL, completed_at = NULL
            WHERE id = ? AND status = ?
            """,
            (JobStatus.QUEUED.value, job_id, JobStatus.FAILED.value),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def batch_force_reset_stuck(self, threshold_hours: int = 2) -> tuple[int, int]:
        """Reset all stuck jobs back to queued state or fail them if max attempts exceeded.

        Args:
            threshold_hours: Hours after which a running job is considered stuck.

        Returns:
            Tuple of (jobs_requeued, jobs_failed).
        """
        threshold = (datetime.now() - timedelta(hours=threshold_hours)).isoformat()
        now = datetime.now().isoformat()

        # First, fail jobs that have exceeded max attempts
        cursor = self.conn.execute(
            """
            UPDATE job_queue
            SET status = ?, error_message = 'Max attempts exceeded (job stuck repeatedly)',
                completed_at = ?
            WHERE status = ? AND started_at < ? AND attempts >= max_attempts
            """,
            (JobStatus.FAILED.value, now, JobStatus.RUNNING.value, threshold),
        )
        jobs_failed = cursor.rowcount

        # Then, requeue jobs that still have retries remaining
        cursor = self.conn.execute(
            """
            UPDATE job_queue
            SET status = ?, started_at = NULL, error_message = NULL
            WHERE status = ? AND started_at < ? AND attempts < max_attempts
            """,
            (JobStatus.QUEUED.value, JobStatus.RUNNING.value, threshold),
        )
        jobs_requeued = cursor.rowcount

        self.conn.commit()
        return jobs_requeued, jobs_failed

    def batch_retry_failed(self) -> int:
        """Retry all failed jobs.

        Returns:
            Number of jobs reset.
        """
        cursor = self.conn.execute(
            """
            UPDATE job_queue
            SET status = ?, attempts = 0, error_message = NULL,
                next_retry_at = NULL, completed_at = NULL
            WHERE status = ?
            """,
            (JobStatus.QUEUED.value, JobStatus.FAILED.value),
        )
        self.conn.commit()
        return cursor.rowcount

    def count_stuck_jobs(self, threshold_hours: int = 2) -> int:
        """Count jobs that have been running longer than threshold.

        Args:
            threshold_hours: Hours after which a running job is considered stuck.

        Returns:
            Number of stuck jobs.
        """
        threshold = (datetime.now() - timedelta(hours=threshold_hours)).isoformat()
        cursor = self.conn.execute(
            """
            SELECT COUNT(*) FROM job_queue
            WHERE status = ? AND started_at < ?
            """,
            (JobStatus.RUNNING.value, threshold),
        )
        return cursor.fetchone()[0]


class SettingsRepository:
    """Repository for runtime settings overrides."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, key: str) -> Optional[str]:
        """Get a setting value by key."""
        cursor = self.conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def get_all(self) -> dict[str, str]:
        """Get all settings as a dictionary."""
        cursor = self.conn.execute("SELECT key, value FROM settings")
        return dict(cursor.fetchall())

    def set(self, key: str, value: str) -> None:
        """Set a setting value (insert or update)."""
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?
            """,
            (key, value, now, value, now),
        )
        self.conn.commit()

    def delete(self, key: str) -> bool:
        """Delete a setting (revert to default)."""
        cursor = self.conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        self.conn.commit()
        return cursor.rowcount > 0

    def set_many(self, settings: dict[str, str]) -> None:
        """Set multiple settings at once."""
        now = datetime.now().isoformat()
        for key, value in settings.items():
            self.conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?
                """,
                (key, value, now, value, now),
            )
        self.conn.commit()


@dataclass
class WhisperModel:
    """A whisper model configuration."""

    id: str
    backend: str
    hf_repo: Optional[str]
    description: Optional[str]
    size_mb: Optional[int]
    is_enabled: bool

    @classmethod
    def from_row(cls, row) -> "WhisperModel":
        """Create from database row."""
        return cls(
            id=row[0],
            backend=row[1],
            hf_repo=row[2],
            description=row[3],
            size_mb=row[4],
            is_enabled=bool(row[5]),
        )


class WhisperModelRepository:
    """Repository for whisper model configurations."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_all(self, enabled_only: bool = True) -> list[WhisperModel]:
        """Get all models."""
        if enabled_only:
            cursor = self.conn.execute(
                "SELECT id, backend, hf_repo, description, size_mb, is_enabled FROM whisper_models WHERE is_enabled = 1 ORDER BY id"
            )
        else:
            cursor = self.conn.execute(
                "SELECT id, backend, hf_repo, description, size_mb, is_enabled FROM whisper_models ORDER BY id"
            )
        return [WhisperModel.from_row(row) for row in cursor.fetchall()]

    def get_by_id(self, model_id: str) -> Optional[WhisperModel]:
        """Get a model by ID."""
        cursor = self.conn.execute(
            "SELECT id, backend, hf_repo, description, size_mb, is_enabled FROM whisper_models WHERE id = ?",
            (model_id,),
        )
        row = cursor.fetchone()
        return WhisperModel.from_row(row) if row else None

    def upsert(
        self,
        model_id: str,
        backend: str,
        hf_repo: Optional[str] = None,
        description: Optional[str] = None,
        size_mb: Optional[int] = None,
        is_enabled: bool = True,
    ) -> None:
        """Insert or update a model."""
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            INSERT INTO whisper_models (id, backend, hf_repo, description, size_mb, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                backend = ?, hf_repo = ?, description = ?, size_mb = ?, is_enabled = ?
            """,
            (model_id, backend, hf_repo, description, size_mb, int(is_enabled), now,
             backend, hf_repo, description, size_mb, int(is_enabled)),
        )
        self.conn.commit()

    def delete(self, model_id: str) -> bool:
        """Delete a model."""
        cursor = self.conn.execute("DELETE FROM whisper_models WHERE id = ?", (model_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def seed_defaults(self) -> int:
        """Seed the default models if table is empty."""
        cursor = self.conn.execute("SELECT COUNT(*) FROM whisper_models")
        if cursor.fetchone()[0] > 0:
            return 0

        default_models = [
            ("tiny", "both", "mlx-community/whisper-tiny", "Fastest, least accurate", 75),
            ("tiny.en", "both", "mlx-community/whisper-tiny.en-mlx", "English-only tiny", 75),
            ("base", "both", "mlx-community/whisper-base-mlx", "Fast, good accuracy", 142),
            ("base.en", "both", "mlx-community/whisper-base.en-mlx", "English-only base", 142),
            ("small", "both", "mlx-community/whisper-small-mlx", "Balanced speed/accuracy", 466),
            ("small.en", "both", "mlx-community/whisper-small.en-mlx", "English-only small", 466),
            ("medium", "both", "mlx-community/whisper-medium-mlx", "High accuracy", 1500),
            ("medium.en", "both", "mlx-community/whisper-medium.en-mlx", "English-only medium", 1500),
            ("large-v2", "both", "mlx-community/whisper-large-v2-mlx", "Previous best accuracy", 3000),
            ("large-v3", "both", "mlx-community/whisper-large-v3-mlx", "Best accuracy", 3000),
            ("large-v3-turbo", "both", "mlx-community/whisper-large-v3-turbo", "Fast large model", 1600),
        ]

        now = datetime.now().isoformat()
        for model_id, backend, hf_repo, description, size_mb in default_models:
            self.conn.execute(
                """
                INSERT INTO whisper_models (id, backend, hf_repo, description, size_mb, is_enabled, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (model_id, backend, hf_repo, description, size_mb, now),
            )
        self.conn.commit()
        return len(default_models)


class TranscriberNodeRepository:
    """Repository for transcriber node operations."""

    NODE_COLUMNS = """id, name, url, api_key, whisper_model, whisper_backend,
                      status, last_heartbeat, current_job_id, priority,
                      created_at, updated_at"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        node_id: str,
        name: str,
        url: str,
        api_key: str,
        whisper_model: str | None = None,
        whisper_backend: str | None = None,
        priority: int = 10,
    ) -> TranscriberNode:
        """Create a new transcriber node."""
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            INSERT INTO transcriber_node (
                id, name, url, api_key, whisper_model, whisper_backend,
                status, priority, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (node_id, name, url, api_key, whisper_model, whisper_backend,
             NodeStatus.OFFLINE.value, priority, now, now),
        )
        self.conn.commit()
        return self.get_by_id(node_id)

    def get_by_id(self, node_id: str) -> Optional[TranscriberNode]:
        """Get node by ID."""
        cursor = self.conn.execute(
            f"SELECT {self.NODE_COLUMNS} FROM transcriber_node WHERE id = ?",
            (node_id,),
        )
        row = cursor.fetchone()
        return TranscriberNode.from_row(row) if row else None

    def get_by_api_key(self, api_key: str) -> Optional[TranscriberNode]:
        """Get node by API key."""
        cursor = self.conn.execute(
            f"SELECT {self.NODE_COLUMNS} FROM transcriber_node WHERE api_key = ?",
            (api_key,),
        )
        row = cursor.fetchone()
        return TranscriberNode.from_row(row) if row else None

    def get_all(self) -> list[TranscriberNode]:
        """Get all nodes."""
        cursor = self.conn.execute(
            f"SELECT {self.NODE_COLUMNS} FROM transcriber_node ORDER BY priority, name"
        )
        return [TranscriberNode.from_row(row) for row in cursor.fetchall()]

    def get_online(self) -> list[TranscriberNode]:
        """Get all online nodes."""
        cursor = self.conn.execute(
            f"""
            SELECT {self.NODE_COLUMNS} FROM transcriber_node
            WHERE status IN (?, ?)
            ORDER BY priority, name
            """,
            (NodeStatus.ONLINE.value, NodeStatus.BUSY.value),
        )
        return [TranscriberNode.from_row(row) for row in cursor.fetchall()]

    def update_status(
        self,
        node_id: str,
        status: NodeStatus,
        current_job_id: int | None = None,
    ) -> None:
        """Update node status."""
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            UPDATE transcriber_node
            SET status = ?, current_job_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (status.value, current_job_id, now, node_id),
        )
        self.conn.commit()

    def update_heartbeat(self, node_id: str, timestamp: datetime | None = None) -> None:
        """Update last heartbeat timestamp.

        Args:
            node_id: The node ID to update.
            timestamp: Optional timestamp to use (default: current time).
        """
        ts = (timestamp or datetime.now()).isoformat()
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            UPDATE transcriber_node
            SET last_heartbeat = ?, updated_at = ?
            WHERE id = ?
            """,
            (ts, now, node_id),
        )
        self.conn.commit()

    def update_info(
        self,
        node_id: str,
        whisper_model: str | None = None,
        whisper_backend: str | None = None,
    ) -> None:
        """Update node whisper info."""
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            UPDATE transcriber_node
            SET whisper_model = ?, whisper_backend = ?, updated_at = ?
            WHERE id = ?
            """,
            (whisper_model, whisper_backend, now, node_id),
        )
        self.conn.commit()

    def delete(self, node_id: str) -> bool:
        """Delete a node."""
        cursor = self.conn.execute(
            "DELETE FROM transcriber_node WHERE id = ?",
            (node_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_stale_nodes(self, timeout_seconds: int = 60) -> list[TranscriberNode]:
        """Get nodes that haven't sent a heartbeat within the timeout.

        Args:
            timeout_seconds: Seconds after which a node is considered stale.

        Returns:
            List of stale nodes.
        """
        threshold = (datetime.now() - timedelta(seconds=timeout_seconds)).isoformat()
        cursor = self.conn.execute(
            f"""
            SELECT {self.NODE_COLUMNS} FROM transcriber_node
            WHERE status != ?
            AND (last_heartbeat IS NULL OR last_heartbeat < ?)
            """,
            (NodeStatus.OFFLINE.value, threshold),
        )
        return [TranscriberNode.from_row(row) for row in cursor.fetchall()]

    def mark_offline(self, node_id: str) -> None:
        """Mark a node as offline and clear its current job."""
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            UPDATE transcriber_node
            SET status = ?, current_job_id = NULL, updated_at = ?
            WHERE id = ?
            """,
            (NodeStatus.OFFLINE.value, now, node_id),
        )
        self.conn.commit()

    def count_by_status(self) -> dict[str, int]:
        """Count nodes by status."""
        cursor = self.conn.execute(
            """
            SELECT status, COUNT(*) FROM transcriber_node
            GROUP BY status
            """
        )
        return dict(cursor.fetchall())
