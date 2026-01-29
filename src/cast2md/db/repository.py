"""Repository classes for database operations."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

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
from cast2md.db.sql import execute, now_sql, ph, phs

# Type alias for database connection (psycopg2)
Connection = Any


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

    def get_by_id(self, feed_id: int) -> Optional[Feed]:
        """Get feed by ID."""
        cursor = execute(
            self.conn,
            f"SELECT {self.FEED_COLUMNS} FROM feed WHERE id = %s",
            (feed_id,),
        )
        row = cursor.fetchone()
        return Feed.from_row(row) if row else None

    def get_by_url(self, url: str) -> Optional[Feed]:
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


class EpisodeRepository:
    """Repository for Episode CRUD operations."""

    # Columns in the order expected by Episode.from_row
    EPISODE_COLUMNS = """id, feed_id, guid, title, description, audio_url, duration_seconds,
                         published_at, status, audio_path, transcript_path, transcript_url,
                         transcript_model, transcript_source, transcript_type,
                         pocketcasts_transcript_url, transcript_checked_at, next_transcript_retry_at,
                         transcript_failure_reason, link, author,
                         error_message, permanent_failure, created_at, updated_at"""

    def __init__(self, conn: Connection):
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

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO episode (
                feed_id, guid, title, description, audio_url,
                duration_seconds, published_at, status, transcript_url,
                transcript_type, link, author, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                feed_id, guid, title, description, audio_url,
                duration_seconds, published_str, EpisodeStatus.NEW.value,
                transcript_url, transcript_type, link, author, now, now,
            ),
        )
        episode_id = cursor.fetchone()[0]

        # Index in PostgreSQL FTS table
        cursor.execute(
            """
            INSERT INTO episode_search (episode_id, feed_id, title_search, description_search)
            VALUES (%s, %s, to_tsvector('english', %s), to_tsvector('english', %s))
            """,
            (episode_id, feed_id, title, description or ""),
        )

        self.conn.commit()
        return self.get_by_id(episode_id)

    def get_by_id(self, episode_id: int) -> Optional[Episode]:
        """Get episode by ID."""
        cursor = execute(
            self.conn,
            f"SELECT {self.EPISODE_COLUMNS} FROM episode WHERE id = %s",
            (episode_id,),
        )
        row = cursor.fetchone()
        return Episode.from_row(row) if row else None

    def get_by_guid(self, feed_id: int, guid: str) -> Optional[Episode]:
        """Get episode by feed ID and GUID."""
        cursor = execute(
            self.conn,
            f"SELECT {self.EPISODE_COLUMNS} FROM episode WHERE feed_id = %s AND guid = %s",
            (feed_id, guid),
        )
        row = cursor.fetchone()
        return Episode.from_row(row) if row else None

    def get_by_feed(self, feed_id: int, limit: int = 50) -> list[Episode]:
        """Get episodes for a feed, ordered by published date descending."""
        cursor = execute(
            self.conn,
            f"""
            SELECT {self.EPISODE_COLUMNS} FROM episode
            WHERE feed_id = %s
            ORDER BY published_at DESC
            LIMIT %s
            """,
            (feed_id, limit),
        )
        return [Episode.from_row(row) for row in cursor.fetchall()]

    def get_by_feed_paginated(
        self,
        feed_id: int,
        limit: int = 25,
        offset: int = 0,
        exclude_permanent_failures: bool = False,
    ) -> list[Episode]:
        """Get episodes with proper SQL OFFSET pagination."""
        pf_clause = " AND permanent_failure = FALSE" if exclude_permanent_failures else ""
        cursor = execute(
            self.conn,
            f"""
            SELECT {self.EPISODE_COLUMNS} FROM episode
            WHERE feed_id = %s{pf_clause}
            ORDER BY published_at DESC
            LIMIT %s OFFSET %s
            """,
            (feed_id, limit, offset),
        )
        return [Episode.from_row(row) for row in cursor.fetchall()]

    def get_by_status(self, status: EpisodeStatus, limit: int = 100) -> list[Episode]:
        """Get episodes by status."""
        cursor = execute(
            self.conn,
            f"""
            SELECT {self.EPISODE_COLUMNS} FROM episode
            WHERE status = %s
            ORDER BY created_at ASC
            LIMIT %s
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
        execute(
            self.conn,
            """
            UPDATE episode
            SET status = %s, error_message = %s, updated_at = %s
            WHERE id = %s
            """,
            (status.value, error_message, now, episode_id),
        )
        self.conn.commit()

    def mark_permanent_failure(self, episode_id: int) -> None:
        """Mark an episode as permanently failed (e.g., audio 404/410).

        The episode remains in the database but is hidden from default views.
        """
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            UPDATE episode
            SET permanent_failure = TRUE, updated_at = %s
            WHERE id = %s
            """,
            (now, episode_id),
        )
        self.conn.commit()

    def count_permanent_failures(self, feed_id: int) -> int:
        """Count permanently failed episodes for a feed."""
        cursor = execute(
            self.conn,
            "SELECT COUNT(*) FROM episode WHERE feed_id = %s AND permanent_failure = TRUE",
            (feed_id,),
        )
        return cursor.fetchone()[0]

    def update_audio_path(self, episode_id: int, audio_path: str | None) -> None:
        """Update episode audio path.

        Args:
            episode_id: Episode ID to update.
            audio_path: Path to audio file, or None to clear.
        """
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            UPDATE episode
            SET audio_path = %s, updated_at = %s
            WHERE id = %s
            """,
            (audio_path, now, episode_id),
        )
        self.conn.commit()

    def update_audio_url(self, episode_id: int, audio_url: str) -> None:
        """Update episode audio URL.

        Used when refreshing expired/signed URLs from the feed.
        """
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            UPDATE episode
            SET audio_url = %s, updated_at = %s
            WHERE id = %s
            """,
            (audio_url, now, episode_id),
        )
        self.conn.commit()

    def update_transcript_path(self, episode_id: int, transcript_path: str) -> None:
        """Update episode transcript path."""
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            UPDATE episode
            SET transcript_path = %s, updated_at = %s
            WHERE id = %s
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
        execute(
            self.conn,
            """
            UPDATE episode
            SET transcript_path = %s, transcript_model = %s, transcript_source = 'whisper',
                updated_at = %s
            WHERE id = %s
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
        execute(
            self.conn,
            """
            UPDATE episode
            SET transcript_path = %s, transcript_source = %s, transcript_model = NULL,
                updated_at = %s
            WHERE id = %s
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
        execute(
            self.conn,
            """
            UPDATE episode
            SET pocketcasts_transcript_url = %s, updated_at = %s
            WHERE id = %s
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
            status: New status (NEW, AWAITING_TRANSCRIPT, or NEEDS_AUDIO).
            checked_at: When the check was performed (None to clear).
            next_retry_at: When to retry (for AWAITING_TRANSCRIPT), or None.
            failure_reason: Type of failure (e.g., 'forbidden'), or None.
        """
        now = datetime.now().isoformat()
        checked_str = checked_at.isoformat() if checked_at else None
        retry_str = next_retry_at.isoformat() if next_retry_at else None
        execute(
            self.conn,
            """
            UPDATE episode
            SET status = %s, transcript_checked_at = %s, next_transcript_retry_at = %s,
                transcript_failure_reason = %s, updated_at = %s
            WHERE id = %s
            """,
            (status.value, checked_str, retry_str, failure_reason, now, episode_id),
        )
        self.conn.commit()

    def get_episodes_for_transcript_retry(self) -> list[Episode]:
        """Get episodes that are due for transcript retry.

        Returns episodes with:
        - status = 'awaiting_transcript'
        - next_transcript_retry_at <= now

        Returns:
            List of episodes ready for retry.
        """
        now = datetime.now().isoformat()
        cursor = execute(
            self.conn,
            f"""
            SELECT {self.EPISODE_COLUMNS} FROM episode
            WHERE status = %s
              AND next_transcript_retry_at IS NOT NULL
              AND next_transcript_retry_at <= %s
            ORDER BY next_transcript_retry_at ASC
            """,
            (EpisodeStatus.AWAITING_TRANSCRIPT.value, now),
        )
        return [Episode.from_row(row) for row in cursor.fetchall()]

    def get_status_counts_for_feed(self, feed_id: int) -> dict[str, int]:
        """Get episode counts by status for a feed.

        Returns:
            Dict mapping status values to counts.
        """
        cursor = execute(
            self.conn,
            """
            SELECT status, COUNT(*) FROM episode
            WHERE feed_id = %s
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
        cursor = execute(
            self.conn,
            f"""
            SELECT {self.EPISODE_COLUMNS} FROM episode
            WHERE feed_id = %s
              AND status = %s
              AND (transcript_model IS NULL OR transcript_model != %s)
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
        cursor = execute(
            self.conn,
            """
            SELECT COUNT(*) FROM episode
            WHERE feed_id = %s
              AND status = %s
              AND (transcript_model IS NULL OR transcript_model != %s)
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
        cursor = execute(
            self.conn,
            """
            UPDATE episode
            SET audio_path = REPLACE(audio_path, %s, %s),
                updated_at = %s
            WHERE feed_id = %s AND audio_path IS NOT NULL AND audio_path LIKE %s
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
        cursor = execute(
            self.conn,
            """
            UPDATE episode
            SET transcript_path = REPLACE(transcript_path, %s, %s),
                updated_at = %s
            WHERE feed_id = %s AND transcript_path IS NOT NULL AND transcript_path LIKE %s
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
        cursor = execute(
            self.conn,
            "SELECT 1 FROM episode WHERE feed_id = %s AND guid = %s",
            (feed_id, guid),
        )
        return cursor.fetchone() is not None

    def count_by_feed(
        self, feed_id: int, exclude_permanent_failures: bool = False
    ) -> int:
        """Count total episodes for a feed."""
        pf_clause = " AND permanent_failure = FALSE" if exclude_permanent_failures else ""
        cursor = execute(
            self.conn,
            f"SELECT COUNT(*) FROM episode WHERE feed_id = %s{pf_clause}",
            (feed_id,),
        )
        return cursor.fetchone()[0]

    def count_by_feed_and_status(self, feed_id: int, status: EpisodeStatus) -> int:
        """Count episodes for a feed with a specific status."""
        cursor = execute(
            self.conn,
            "SELECT COUNT(*) FROM episode WHERE feed_id = %s AND status = %s",
            (feed_id, status.value),
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
        cursor = execute(
            self.conn,
            "SELECT COUNT(*) FROM episode WHERE feed_id = %s AND transcript_url IS NOT NULL",
            (feed_id,),
        )
        podcast20_count = cursor.fetchone()[0]

        # Count episodes with Pocket Casts transcripts (but no Podcast 2.0)
        cursor = execute(
            self.conn,
            """SELECT COUNT(*) FROM episode
               WHERE feed_id = %s
                 AND transcript_url IS NULL
                 AND pocketcasts_transcript_url IS NOT NULL""",
            (feed_id,),
        )
        pocketcasts_count = cursor.fetchone()[0]

        # Count episodes with neither
        cursor = execute(
            self.conn,
            """SELECT COUNT(*) FROM episode
               WHERE feed_id = %s
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

        Uses full-text search when query is provided for word-boundary matching.

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
            placeholders = ",".join("%s" for _ in episode_ids)
            id_order = " ".join(f"WHEN %s THEN {i}" for i in range(len(episode_ids)))

            if status:
                cursor = self.conn.cursor()
                cursor.execute(
                    f"""
                    SELECT {self.EPISODE_COLUMNS} FROM episode
                    WHERE id IN ({placeholders}) AND status = %s
                    ORDER BY CASE id {id_order} END
                    """,
                    (*episode_ids, status.value, *episode_ids),
                )
                # Recount with status filter
                cursor.execute(
                    f"""
                    SELECT COUNT(*) FROM episode
                    WHERE id IN ({placeholders}) AND status = %s
                    """,
                    (*episode_ids, status.value),
                )
                total = cursor.fetchone()[0]
            else:
                cursor = self.conn.cursor()
                cursor.execute(
                    f"""
                    SELECT {self.EPISODE_COLUMNS} FROM episode
                    WHERE id IN ({placeholders})
                    ORDER BY CASE id {id_order} END
                    """,
                    (*episode_ids, *episode_ids),
                )
                total = fts_total

            episodes = [Episode.from_row(row) for row in cursor.fetchall()]
            return episodes, total

        # No query - use simple SQL filtering
        conditions = ["feed_id = %s"]
        params: list = [feed_id]

        if status:
            conditions.append("status = %s")
            params.append(status.value)

        where_clause = " AND ".join(conditions)

        # Get total count
        count_cursor = execute(
            self.conn,
            f"SELECT COUNT(*) FROM episode WHERE {where_clause}",
            params,
        )
        total = count_cursor.fetchone()[0]

        # Get paginated results
        params.extend([limit, offset])
        cursor = execute(
            self.conn,
            f"""
            SELECT {self.EPISODE_COLUMNS} FROM episode
            WHERE {where_clause}
            ORDER BY published_at DESC
            LIMIT %s OFFSET %s
            """,
            params,
        )
        episodes = [Episode.from_row(row) for row in cursor.fetchall()]

        return episodes, total

    def count_by_status(self) -> dict[str, int]:
        """Count episodes by status."""
        cursor = execute(
            self.conn,
            """
            SELECT status, COUNT(*) FROM episode
            GROUP BY status
            """,
        )
        return dict(cursor.fetchall())

    def delete(self, episode_id: int) -> bool:
        """Delete an episode."""
        # Also remove from FTS index
        execute(self.conn, "DELETE FROM episode_search WHERE episode_id = %s", (episode_id,))

        cursor = execute(self.conn, "DELETE FROM episode WHERE id = %s", (episode_id,))
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
        execute(self.conn, "DELETE FROM episode_search WHERE episode_id = %s", (episode_id,))
        # Insert new entry
        execute(
            self.conn,
            """
            INSERT INTO episode_search (episode_id, feed_id, title_search, description_search)
            VALUES (%s, %s, to_tsvector('english', %s), to_tsvector('english', %s))
            """,
            (episode_id, feed_id, title, description or ""),
        )
        self.conn.commit()

    def reindex_all_episodes(self) -> int:
        """Rebuild the entire episode FTS index from the episode table.

        Returns:
            Number of episodes indexed.
        """
        # Clear existing FTS data
        execute(self.conn, "DELETE FROM episode_search")

        # Index all episodes
        cursor = execute(self.conn, "SELECT id, feed_id, title, description FROM episode")
        count = 0
        for row in cursor.fetchall():
            episode_id, feed_id, title, description = row
            execute(
                self.conn,
                """
                INSERT INTO episode_search (episode_id, feed_id, title_search, description_search)
                VALUES (%s, %s, to_tsvector('english', %s), to_tsvector('english', %s))
                """,
                (episode_id, feed_id, title, description or ""),
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
        """Search episodes using full-text search with flexible OR matching.

        Uses OR between words for flexible matching (quoted phrases use AND).
        Title matches are boosted 3x over description matches.

        Args:
            query: Search query. Supports quoted phrases for exact matching.
            feed_id: Optional feed ID to filter results.
            limit: Maximum results per page.
            offset: Pagination offset.

        Returns:
            (list of episode IDs, total count)
        """
        from cast2md.search.repository import build_flexible_tsquery

        tsquery_str = build_flexible_tsquery(query)
        if not tsquery_str:
            return [], 0

        # PostgreSQL tsvector search with flexible OR matching
        # Title matches are boosted 3x over description matches
        if feed_id is not None:
            count_cursor = execute(
                self.conn,
                """
                SELECT COUNT(*) FROM episode_search
                WHERE (title_search @@ to_tsquery('english', %s)
                       OR description_search @@ to_tsquery('english', %s))
                  AND feed_id = %s
                """,
                (tsquery_str, tsquery_str, feed_id),
            )
            total = count_cursor.fetchone()[0]

            cursor = execute(
                self.conn,
                """
                SELECT episode_id,
                       ts_rank(title_search, to_tsquery('english', %s)) * 3 +
                       ts_rank(description_search, to_tsquery('english', %s)) as rank
                FROM episode_search
                WHERE (title_search @@ to_tsquery('english', %s)
                       OR description_search @@ to_tsquery('english', %s))
                  AND feed_id = %s
                ORDER BY rank DESC
                LIMIT %s OFFSET %s
                """,
                (tsquery_str, tsquery_str, tsquery_str, tsquery_str, feed_id, limit, offset),
            )
        else:
            count_cursor = execute(
                self.conn,
                """
                SELECT COUNT(*) FROM episode_search
                WHERE title_search @@ to_tsquery('english', %s)
                   OR description_search @@ to_tsquery('english', %s)
                """,
                (tsquery_str, tsquery_str),
            )
            total = count_cursor.fetchone()[0]

            cursor = execute(
                self.conn,
                """
                SELECT episode_id,
                       ts_rank(title_search, to_tsquery('english', %s)) * 3 +
                       ts_rank(description_search, to_tsquery('english', %s)) as rank
                FROM episode_search
                WHERE title_search @@ to_tsquery('english', %s)
                   OR description_search @@ to_tsquery('english', %s)
                ORDER BY rank DESC
                LIMIT %s OFFSET %s
                """,
                (tsquery_str, tsquery_str, tsquery_str, tsquery_str, limit, offset),
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
        cursor = execute(
            self.conn,
            f"""
            SELECT {ep_cols}, COALESCE(f.custom_title, f.title) as feed_title
            FROM episode e
            JOIN feed f ON e.feed_id = f.id
            WHERE e.published_at >= %s
            ORDER BY e.published_at DESC
            LIMIT %s
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

    def get_recent_transcribed_episodes(
        self, limit: int = 12
    ) -> list[tuple[Episode, str, str | None]]:
        """Get recently transcribed episodes with feed info.

        Returns completed episodes that have transcripts, sorted by most recently
        transcribed (updated_at DESC).

        Args:
            limit: Maximum number of episodes to return (default: 12).

        Returns:
            List of tuples (Episode, feed_title, feed_image_url) sorted by updated_at DESC.
        """
        # Prefix episode columns with table alias
        ep_cols = ", ".join(f"e.{c.strip()}" for c in self.EPISODE_COLUMNS.split(","))
        cursor = execute(
            self.conn,
            f"""
            SELECT {ep_cols}, COALESCE(f.custom_title, f.title) as feed_title, f.image_url
            FROM episode e
            JOIN feed f ON e.feed_id = f.id
            WHERE e.status = %s AND e.transcript_path IS NOT NULL
            ORDER BY e.updated_at DESC
            LIMIT %s
            """,
            (EpisodeStatus.COMPLETED.value, limit),
        )
        results = []
        for row in cursor.fetchall():
            # Episode columns are all but the last two (feed_title, image_url)
            episode = Episode.from_row(row[:-2])
            feed_title = row[-2]
            image_url = row[-1]
            results.append((episode, feed_title, image_url))
        return results

    def search_episodes_fts_full(
        self,
        query: str,
        feed_id: int | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[Episode], int]:
        """Search episodes using full-text search and return full Episode objects.

        Args:
            query: Search query.
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
        placeholders = ",".join("%s" for _ in episode_ids)
        id_order = " ".join(f"WHEN %s THEN {i}" for i in range(len(episode_ids)))

        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            SELECT {self.EPISODE_COLUMNS} FROM episode
            WHERE id IN ({placeholders})
            ORDER BY CASE id {id_order} END
            """,
            (*episode_ids, *episode_ids),
        )

        episodes = [Episode.from_row(row) for row in cursor.fetchall()]
        return episodes, total


class JobRepository:
    """Repository for Job queue operations."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def create(
        self,
        episode_id: int,
        job_type: JobType,
        priority: int = 10,
        max_attempts: int = 10,
    ) -> Job:
        """Create a new job in the queue."""
        now = datetime.now().isoformat()

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO job_queue (
                episode_id, job_type, priority, status, attempts,
                max_attempts, scheduled_at, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                episode_id, job_type.value, priority, JobStatus.QUEUED.value,
                0, max_attempts, now, now,
            ),
        )
        job_id = cursor.fetchone()[0]

        self.conn.commit()
        return self.get_by_id(job_id)

    def get_by_id(self, job_id: int) -> Optional[Job]:
        """Get job by ID."""
        cursor = execute(
            self.conn,
            "SELECT * FROM job_queue WHERE id = %s",
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
            cursor = execute(
                self.conn,
                """
                SELECT * FROM job_queue
                WHERE job_type = %s
                  AND status = %s
                  AND assigned_node_id IS NULL
                  AND (next_retry_at IS NULL OR next_retry_at <= %s)
                ORDER BY priority ASC, scheduled_at ASC
                LIMIT 1
                """,
                (job_type.value, JobStatus.QUEUED.value, now),
            )
        else:
            cursor = execute(
                self.conn,
                """
                SELECT * FROM job_queue
                WHERE job_type = %s
                  AND status = %s
                  AND (next_retry_at IS NULL OR next_retry_at <= %s)
                ORDER BY priority ASC, scheduled_at ASC
                LIMIT 1
                """,
                (job_type.value, JobStatus.QUEUED.value, now),
            )
        row = cursor.fetchone()
        return Job.from_row(row) if row else None

    def claim_next_job(
        self, job_type: JobType, node_id: str = "local", local_only: bool = False
    ) -> Optional[Job]:
        """Atomically claim the next queued job using UPDATE...RETURNING.

        This prevents race conditions where multiple workers claim the same job.
        Uses a single atomic UPDATE statement with a subquery to select the job.

        Args:
            job_type: Type of job to claim.
            node_id: The node ID claiming this job.
            local_only: If True, only claim jobs not assigned to a node.

        Returns:
            The claimed Job with status set to RUNNING, or None if no jobs available.
        """
        now = datetime.now().isoformat()

        if local_only:
            subquery = """
                SELECT id FROM job_queue
                WHERE job_type = %s
                  AND status = %s
                  AND assigned_node_id IS NULL
                  AND attempts < max_attempts
                  AND (next_retry_at IS NULL OR next_retry_at <= %s)
                ORDER BY priority ASC, scheduled_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            """
        else:
            subquery = """
                SELECT id FROM job_queue
                WHERE job_type = %s
                  AND status = %s
                  AND attempts < max_attempts
                  AND (next_retry_at IS NULL OR next_retry_at <= %s)
                ORDER BY priority ASC, scheduled_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            """

        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            UPDATE job_queue
            SET status = %s,
                started_at = %s,
                attempts = attempts + 1,
                progress_percent = 0,
                assigned_node_id = %s,
                claimed_at = %s
            WHERE id = ({subquery})
            RETURNING *
            """,
            (JobStatus.RUNNING.value, now, node_id, now, job_type.value, JobStatus.QUEUED.value, now),
        )

        row = cursor.fetchone()
        self.conn.commit()
        return Job.from_row(row) if row else None

    def get_next_unclaimed_job(self, job_type: JobType) -> Optional[Job]:
        """Get the next queued job that hasn't been claimed by any node.

        Used by distributed transcription nodes to claim work.
        """
        now = datetime.now().isoformat()
        cursor = execute(
            self.conn,
            """
            SELECT * FROM job_queue
            WHERE job_type = %s
              AND status = %s
              AND assigned_node_id IS NULL
              AND attempts < max_attempts
              AND (next_retry_at IS NULL OR next_retry_at <= %s)
            ORDER BY priority ASC, scheduled_at ASC
            LIMIT 1
            """,
            (job_type.value, JobStatus.QUEUED.value, now),
        )
        row = cursor.fetchone()
        return Job.from_row(row) if row else None

    def claim_job(self, job_id: int, node_id: str) -> None:
        """Claim a job for a specific node.

        If the job has already exceeded max_attempts, it will be marked as failed
        instead of claimed.
        """
        import logging

        logger = logging.getLogger(__name__)

        # Check if job has exceeded max_attempts
        job = self.get_by_id(job_id)
        if job and job.attempts >= job.max_attempts:
            logger.warning(
                f"Job {job_id} has {job.attempts}/{job.max_attempts} attempts, failing instead of claiming"
            )
            self.mark_failed(job_id, "Max attempts exceeded", retry=False)
            return

        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            UPDATE job_queue
            SET assigned_node_id = %s, claimed_at = %s, status = %s, started_at = %s,
                attempts = attempts + 1, progress_percent = 0
            WHERE id = %s
            """,
            (node_id, now, JobStatus.RUNNING.value, now, job_id),
        )
        self.conn.commit()

    def unclaim_job(self, job_id: int) -> None:
        """Remove node assignment from a job (for retries or failed nodes)."""
        execute(
            self.conn,
            """
            UPDATE job_queue
            SET assigned_node_id = NULL, claimed_at = NULL
            WHERE id = %s
            """,
            (job_id,),
        )
        self.conn.commit()

    def resync_job(self, job_id: int, node_id: str) -> None:
        """Reassign a job to a node without incrementing attempts.

        Used to restore job assignment after server restart when a node
        reports it's still working on a job via heartbeat.
        """
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            UPDATE job_queue
            SET assigned_node_id = %s, claimed_at = %s
            WHERE id = %s
            """,
            (node_id, now, job_id),
        )
        self.conn.commit()

    def get_jobs_by_node(self, node_id: str) -> list[Job]:
        """Get all jobs assigned to a specific node."""
        cursor = execute(
            self.conn,
            """
            SELECT * FROM job_queue
            WHERE assigned_node_id = %s
            ORDER BY claimed_at DESC
            """,
            (node_id,),
        )
        return [Job.from_row(row) for row in cursor.fetchall()]

    def release_job(self, job_id: int) -> None:
        """Release a job back to the queue for another worker to pick up.

        Resets the job to queued status and clears assignment fields.
        Does not increment attempts since the job wasn't actually processed.

        If the job has exceeded max_attempts, it will be marked as failed instead.
        """
        import logging

        logger = logging.getLogger(__name__)

        # Check if job has exceeded max_attempts
        job = self.get_by_id(job_id)
        if job and job.attempts >= job.max_attempts:
            logger.warning(
                f"Job {job_id} has {job.attempts}/{job.max_attempts} attempts, failing instead of releasing"
            )
            self.mark_failed(job_id, "Max attempts exceeded", retry=False)
            return

        execute(
            self.conn,
            """
            UPDATE job_queue
            SET status = %s, assigned_node_id = NULL, claimed_at = NULL,
                started_at = NULL, progress_percent = NULL
            WHERE id = %s
            """,
            (JobStatus.QUEUED.value, job_id),
        )
        self.conn.commit()

    def reclaim_stale_jobs(self, timeout_minutes: int = 30) -> tuple[int, int]:
        """Reclaim jobs that have been running too long on a node.

        Jobs that have been running longer than timeout_minutes on a node
        are either reset to queued state (if retries remain) or marked as
        permanently failed (if max attempts exceeded).

        Returns:
            Tuple of (jobs_requeued, jobs_failed).
        """
        threshold = (datetime.now() - timedelta(minutes=timeout_minutes)).isoformat()
        now = datetime.now().isoformat()

        # First, fail jobs that have exceeded max attempts
        # Use started_at (not claimed_at) so reclaim cycles don't reset the timeout
        cursor = execute(
            self.conn,
            """
            UPDATE job_queue
            SET status = %s, error_message = 'Max attempts exceeded (job timed out repeatedly)',
                completed_at = %s, assigned_node_id = NULL, claimed_at = NULL
            WHERE status = %s
              AND assigned_node_id IS NOT NULL
              AND started_at < %s
              AND attempts >= max_attempts
            """,
            (JobStatus.FAILED.value, now, JobStatus.RUNNING.value, threshold),
        )
        jobs_failed = cursor.rowcount

        # Then, requeue jobs that still have retries remaining
        cursor = execute(
            self.conn,
            """
            UPDATE job_queue
            SET status = %s, assigned_node_id = NULL, claimed_at = NULL, started_at = NULL
            WHERE status = %s
              AND assigned_node_id IS NOT NULL
              AND started_at < %s
              AND attempts < max_attempts
            """,
            (JobStatus.QUEUED.value, JobStatus.RUNNING.value, threshold),
        )
        jobs_requeued = cursor.rowcount

        self.conn.commit()
        return jobs_requeued, jobs_failed

    def get_running_jobs(self, job_type: JobType) -> list[Job]:
        """Get all running jobs of given type."""
        cursor = execute(
            self.conn,
            """
            SELECT * FROM job_queue
            WHERE job_type = %s AND status = %s
            ORDER BY started_at ASC
            """,
            (job_type.value, JobStatus.RUNNING.value),
        )
        return [Job.from_row(row) for row in cursor.fetchall()]

    def get_queued_jobs(self, job_type: JobType | None = None, limit: int = 100) -> list[Job]:
        """Get queued jobs ready to run (excludes jobs waiting for retry)."""
        now = datetime.now().isoformat()
        if job_type:
            cursor = execute(
                self.conn,
                """
                SELECT * FROM job_queue
                WHERE job_type = %s AND status = %s
                  AND (next_retry_at IS NULL OR next_retry_at <= %s)
                ORDER BY priority ASC, scheduled_at ASC
                LIMIT %s
                """,
                (job_type.value, JobStatus.QUEUED.value, now, limit),
            )
        else:
            cursor = execute(
                self.conn,
                """
                SELECT * FROM job_queue
                WHERE status = %s
                  AND (next_retry_at IS NULL OR next_retry_at <= %s)
                ORDER BY priority ASC, scheduled_at ASC
                LIMIT %s
                """,
                (JobStatus.QUEUED.value, now, limit),
            )
        return [Job.from_row(row) for row in cursor.fetchall()]

    def get_by_episode(self, episode_id: int) -> list[Job]:
        """Get all jobs for an episode."""
        cursor = execute(
            self.conn,
            """
            SELECT * FROM job_queue
            WHERE episode_id = %s
            ORDER BY created_at DESC
            """,
            (episode_id,),
        )
        return [Job.from_row(row) for row in cursor.fetchall()]

    def has_pending_job(self, episode_id: int, job_type: JobType) -> bool:
        """Check if episode has a pending or running job of given type."""
        cursor = execute(
            self.conn,
            """
            SELECT 1 FROM job_queue
            WHERE episode_id = %s AND job_type = %s AND status IN (%s, %s)
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
        execute(
            self.conn,
            """
            UPDATE job_queue
            SET status = %s, started_at = %s, attempts = attempts + 1,
                progress_percent = 0, assigned_node_id = %s, claimed_at = %s
            WHERE id = %s
            """,
            (JobStatus.RUNNING.value, now, node_id, now, job_id),
        )
        self.conn.commit()

    def mark_completed(self, job_id: int) -> None:
        """Mark a job as completed."""
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            UPDATE job_queue
            SET status = %s, completed_at = %s, progress_percent = 100
            WHERE id = %s
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
        execute(
            self.conn,
            """
            UPDATE job_queue
            SET progress_percent = %s
            WHERE id = %s
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

        # Find running jobs WITHOUT assigned nodes (local server jobs only).
        # Jobs with assigned_node_id set are being processed by remote nodes
        # and should be left alone - the coordinator's job timeout will reclaim
        # them if the node truly died.
        cursor = execute(
            self.conn,
            """
            SELECT id, episode_id, job_type, attempts, max_attempts FROM job_queue
            WHERE status = %s AND assigned_node_id IS NULL
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
            placeholders = ",".join("%s" for _ in job_ids)
            execute(
                self.conn,
                f"""
                UPDATE job_queue
                SET status = %s, error_message = 'Max attempts exceeded (orphaned on restart)',
                    completed_at = %s, assigned_node_id = NULL, claimed_at = NULL,
                    progress_percent = NULL
                WHERE id IN ({placeholders})
                """,
                [JobStatus.FAILED.value, now] + job_ids,
            )

            # Set episode status to failed
            for job_id, episode_id, job_type in jobs_to_fail:
                execute(
                    self.conn,
                    "UPDATE episode SET status = %s, error_message = %s WHERE id = %s",
                    (EpisodeStatus.FAILED.value, "Max attempts exceeded", episode_id),
                )

        # Requeue jobs that still have retries
        if jobs_to_requeue:
            job_ids = [j[0] for j in jobs_to_requeue]
            placeholders = ",".join("%s" for _ in job_ids)
            execute(
                self.conn,
                f"""
                UPDATE job_queue
                SET status = %s, started_at = NULL, assigned_node_id = NULL,
                    claimed_at = NULL, progress_percent = NULL
                WHERE id IN ({placeholders})
                """,
                [JobStatus.QUEUED.value] + job_ids,
            )

            # Reset episode statuses
            for job_id, episode_id, job_type in jobs_to_requeue:
                if job_type == JobType.DOWNLOAD.value:
                    execute(
                        self.conn,
                        "UPDATE episode SET status = %s WHERE id = %s",
                        (EpisodeStatus.NEW.value, episode_id),
                    )
                elif job_type == JobType.TRANSCRIBE.value:
                    execute(
                        self.conn,
                        "UPDATE episode SET status = %s WHERE id = %s",
                        (EpisodeStatus.AUDIO_READY.value, episode_id),
                    )
                elif job_type == JobType.TRANSCRIPT_DOWNLOAD.value:
                    # Transcript download jobs don't change episode status during processing
                    # Episode stays in NEW until transcript is found or user queues download
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
            backoff_minutes = min(5 ** job.attempts, 720)
            next_retry = now + timedelta(minutes=backoff_minutes)

            execute(
                self.conn,
                """
                UPDATE job_queue
                SET status = %s, error_message = %s, next_retry_at = %s
                WHERE id = %s
                """,
                (JobStatus.QUEUED.value, error_message, next_retry.isoformat(), job_id),
            )
        else:
            # Max attempts reached, mark as failed
            execute(
                self.conn,
                """
                UPDATE job_queue
                SET status = %s, error_message = %s, completed_at = %s
                WHERE id = %s
                """,
                (JobStatus.FAILED.value, error_message, now.isoformat(), job_id),
            )
        self.conn.commit()

    def count_by_status(self, job_type: JobType | None = None) -> dict[str, int]:
        """Count jobs by status."""
        if job_type:
            cursor = execute(
                self.conn,
                """
                SELECT status, COUNT(*) FROM job_queue
                WHERE job_type = %s
                GROUP BY status
                """,
                (job_type.value,),
            )
        else:
            cursor = execute(
                self.conn,
                """
                SELECT status, COUNT(*) FROM job_queue
                GROUP BY status
                """,
            )
        return dict(cursor.fetchall())

    def delete(self, job_id: int) -> bool:
        """Delete a job."""
        cursor = execute(self.conn, "DELETE FROM job_queue WHERE id = %s", (job_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def cancel_queued(self, job_id: int) -> bool:
        """Cancel a queued job (only if not running)."""
        cursor = execute(
            self.conn,
            """
            DELETE FROM job_queue
            WHERE id = %s AND status = %s
            """,
            (job_id, JobStatus.QUEUED.value),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def cleanup_completed(self, older_than_days: int = 7) -> int:
        """Delete completed/failed jobs older than N days."""
        cutoff = (datetime.now() - timedelta(days=older_than_days)).isoformat()

        cursor = execute(
            self.conn,
            """
            DELETE FROM job_queue
            WHERE status IN (%s, %s) AND completed_at < %s
            """,
            (JobStatus.COMPLETED.value, JobStatus.FAILED.value, cutoff),
        )
        self.conn.commit()
        return cursor.rowcount

    def get_stuck_jobs(self, threshold_minutes: int = 2) -> list[Job]:
        """Get jobs that have been running longer than threshold.

        Args:
            threshold_minutes: Hours after which a running job is considered stuck.

        Returns:
            List of stuck jobs.
        """
        threshold = (datetime.now() - timedelta(minutes=threshold_minutes)).isoformat()
        cursor = execute(
            self.conn,
            """
            SELECT * FROM job_queue
            WHERE status = %s
            AND started_at < %s
            ORDER BY started_at ASC
            """,
            (JobStatus.RUNNING.value, threshold),
        )
        return [Job.from_row(row) for row in cursor.fetchall()]

    def force_reset(self, job_id: int) -> bool:
        """Force reset a running/stuck job back to queued state.

        Clears started_at, assigned_node_id, claimed_at and resets status to queued.
        If the job has exceeded max_attempts, it will be marked as failed instead.

        Args:
            job_id: Job ID to reset.

        Returns:
            True if job was reset or failed, False if not found or not in running state.
        """
        import logging

        logger = logging.getLogger(__name__)

        # Check if job has exceeded max_attempts
        job = self.get_by_id(job_id)
        if not job or job.status != JobStatus.RUNNING:
            return False

        if job.attempts >= job.max_attempts:
            logger.warning(
                f"Job {job_id} has {job.attempts}/{job.max_attempts} attempts, failing instead of resetting"
            )
            self.mark_failed(job_id, "Max attempts exceeded", retry=False)
            return True

        cursor = execute(
            self.conn,
            """
            UPDATE job_queue
            SET status = %s, started_at = NULL, error_message = NULL,
                assigned_node_id = NULL, claimed_at = NULL, progress_percent = 0
            WHERE id = %s AND status = %s
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
        stuck_threshold_minutes: int = 2,
    ) -> list[Job]:
        """Get all jobs with optional filters.

        Args:
            status: Filter by job status.
            job_type: Filter by job type.
            limit: Maximum number of jobs to return.
            include_stuck: If True and status is None, includes stuck indicator.
            stuck_threshold_minutes: Hours after which running job is stuck.

        Returns:
            List of jobs ordered by priority, then scheduled time.
        """
        conditions = []
        params = []

        if status:
            conditions.append("status = %s")
            params.append(status.value)

        if job_type:
            conditions.append("job_type = %s")
            params.append(job_type.value)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        params.append(limit)
        cursor = execute(
            self.conn,
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
            LIMIT %s
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
        cursor = execute(
            self.conn,
            """
            SELECT * FROM job_queue
            WHERE status = %s
            ORDER BY completed_at DESC
            LIMIT %s
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
        cursor = execute(
            self.conn,
            """
            UPDATE job_queue
            SET status = %s, attempts = 0, error_message = NULL,
                next_retry_at = NULL, completed_at = NULL
            WHERE id = %s AND status = %s
            """,
            (JobStatus.QUEUED.value, job_id, JobStatus.FAILED.value),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def batch_force_reset_stuck(self, threshold_minutes: int = 2) -> tuple[int, int]:
        """Reset all stuck jobs back to queued state or fail them if max attempts exceeded.

        Args:
            threshold_minutes: Hours after which a running job is considered stuck.

        Returns:
            Tuple of (jobs_requeued, jobs_failed).
        """
        threshold = (datetime.now() - timedelta(minutes=threshold_minutes)).isoformat()
        now = datetime.now().isoformat()

        # First, fail jobs that have exceeded max attempts
        cursor = execute(
            self.conn,
            """
            UPDATE job_queue
            SET status = %s, error_message = 'Max attempts exceeded (job stuck repeatedly)',
                completed_at = %s
            WHERE status = %s AND started_at < %s AND attempts >= max_attempts
            """,
            (JobStatus.FAILED.value, now, JobStatus.RUNNING.value, threshold),
        )
        jobs_failed = cursor.rowcount

        # Then, requeue jobs that still have retries remaining
        cursor = execute(
            self.conn,
            """
            UPDATE job_queue
            SET status = %s, started_at = NULL, error_message = NULL
            WHERE status = %s AND started_at < %s AND attempts < max_attempts
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
        cursor = execute(
            self.conn,
            """
            UPDATE job_queue
            SET status = %s, attempts = 0, error_message = NULL,
                next_retry_at = NULL, completed_at = NULL
            WHERE status = %s
            """,
            (JobStatus.QUEUED.value, JobStatus.FAILED.value),
        )
        self.conn.commit()
        return cursor.rowcount

    def count_stuck_jobs(self, threshold_minutes: int = 2) -> int:
        """Count jobs that have been running longer than threshold.

        Args:
            threshold_minutes: Hours after which a running job is considered stuck.

        Returns:
            Number of stuck jobs.
        """
        threshold = (datetime.now() - timedelta(minutes=threshold_minutes)).isoformat()
        cursor = execute(
            self.conn,
            """
            SELECT COUNT(*) FROM job_queue
            WHERE status = %s AND started_at < %s
            """,
            (JobStatus.RUNNING.value, threshold),
        )
        return cursor.fetchone()[0]

    def get_completed_jobs_stats(
        self,
        hours: int = 24,
        job_type: JobType | None = None,
    ) -> dict:
        """Get statistics for completed jobs within a time window.

        Args:
            hours: Number of hours to look back.
            job_type: Optional job type filter.

        Returns:
            Dict with count, total_duration_seconds, avg_duration_seconds.
        """
        threshold = (datetime.now() - timedelta(hours=hours)).isoformat()

        if job_type:
            cursor = execute(
                self.conn,
                """
                SELECT
                    COUNT(*) as count,
                    COALESCE(SUM(EXTRACT(EPOCH FROM (completed_at - started_at))), 0) as total_seconds,
                    COALESCE(AVG(EXTRACT(EPOCH FROM (completed_at - started_at))), 0) as avg_seconds
                FROM job_queue
                WHERE status = %s
                  AND job_type = %s
                  AND completed_at >= %s
                  AND started_at IS NOT NULL
                """,
                (JobStatus.COMPLETED.value, job_type.value, threshold),
            )
        else:
            cursor = execute(
                self.conn,
                """
                SELECT
                    COUNT(*) as count,
                    COALESCE(SUM(EXTRACT(EPOCH FROM (completed_at - started_at))), 0) as total_seconds,
                    COALESCE(AVG(EXTRACT(EPOCH FROM (completed_at - started_at))), 0) as avg_seconds
                FROM job_queue
                WHERE status = %s
                  AND completed_at >= %s
                  AND started_at IS NOT NULL
                """,
                (JobStatus.COMPLETED.value, threshold),
            )

        row = cursor.fetchone()
        return {
            "count": row[0] or 0,
            "total_duration_seconds": int(row[1] or 0),
            "avg_duration_seconds": int(row[2] or 0),
        }

    def get_stats_by_node(self, hours: int = 24) -> list[dict]:
        """Get completion stats grouped by node.

        Args:
            hours: Number of hours to look back.

        Returns:
            List of dicts with node_id, node_name, count, avg_duration_seconds.
        """
        threshold = (datetime.now() - timedelta(hours=hours)).isoformat()

        cursor = execute(
            self.conn,
            """
            SELECT
                j.assigned_node_id,
                n.name as node_name,
                COUNT(*) as count,
                COALESCE(AVG(EXTRACT(EPOCH FROM (j.completed_at - j.started_at))), 0) as avg_seconds
            FROM job_queue j
            LEFT JOIN transcriber_node n ON j.assigned_node_id = n.id
            WHERE j.status = %s
              AND j.job_type = %s
              AND j.completed_at >= %s
              AND j.started_at IS NOT NULL
              AND j.assigned_node_id IS NOT NULL
            GROUP BY j.assigned_node_id, n.name
            ORDER BY count DESC
            """,
            (JobStatus.COMPLETED.value, JobType.TRANSCRIBE.value, threshold),
        )

        return [
            {
                "node_id": row[0],
                "node_name": row[1] or "Unknown",
                "count": row[2],
                "avg_duration_seconds": int(row[3] or 0),
            }
            for row in cursor.fetchall()
        ]

    def get_audio_minutes_processed(self, hours: int = 24) -> int:
        """Get total audio minutes processed in the time window.

        Args:
            hours: Number of hours to look back.

        Returns:
            Total audio duration in minutes.
        """
        threshold = (datetime.now() - timedelta(hours=hours)).isoformat()

        cursor = execute(
            self.conn,
            """
            SELECT COALESCE(SUM(e.duration_seconds), 0)
            FROM job_queue j
            JOIN episode e ON j.episode_id = e.id
            WHERE j.status = %s
              AND j.job_type = %s
              AND j.completed_at >= %s
            """,
            (JobStatus.COMPLETED.value, JobType.TRANSCRIBE.value, threshold),
        )

        total_seconds = cursor.fetchone()[0] or 0
        return int(total_seconds / 60)


class SettingsRepository:
    """Repository for runtime settings overrides."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def get(self, key: str) -> Optional[str]:
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

    def __init__(self, conn: Connection):
        self.conn = conn

    def get_all(self, enabled_only: bool = True) -> list[WhisperModel]:
        """Get all models."""
        if enabled_only:
            cursor = execute(
                self.conn,
                "SELECT id, backend, hf_repo, description, size_mb, is_enabled FROM whisper_models WHERE is_enabled = TRUE ORDER BY id"
            )
        else:
            cursor = execute(
                self.conn,
                "SELECT id, backend, hf_repo, description, size_mb, is_enabled FROM whisper_models ORDER BY id"
            )
        return [WhisperModel.from_row(row) for row in cursor.fetchall()]

    def get_by_id(self, model_id: str) -> Optional[WhisperModel]:
        """Get a model by ID."""
        cursor = execute(
            self.conn,
            "SELECT id, backend, hf_repo, description, size_mb, is_enabled FROM whisper_models WHERE id = %s",
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
        execute(
            self.conn,
            """
            INSERT INTO whisper_models (id, backend, hf_repo, description, size_mb, is_enabled, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                backend = EXCLUDED.backend, hf_repo = EXCLUDED.hf_repo,
                description = EXCLUDED.description, size_mb = EXCLUDED.size_mb,
                is_enabled = EXCLUDED.is_enabled
            """,
            (model_id, backend, hf_repo, description, size_mb, is_enabled, now),
        )
        self.conn.commit()

    def delete(self, model_id: str) -> bool:
        """Delete a model."""
        cursor = execute(self.conn, "DELETE FROM whisper_models WHERE id = %s", (model_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def seed_defaults(self) -> int:
        """Seed the default models if table is empty."""
        cursor = execute(self.conn, "SELECT COUNT(*) FROM whisper_models")
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
            execute(
                self.conn,
                """
                INSERT INTO whisper_models (id, backend, hf_repo, description, size_mb, is_enabled, created_at)
                VALUES (%s, %s, %s, %s, %s, TRUE, %s)
                """,
                (model_id, backend, hf_repo, description, size_mb, now),
            )
        self.conn.commit()
        return len(default_models)


@dataclass
class RunPodModel:
    """A RunPod transcription model configuration."""

    id: str
    display_name: str
    backend: str  # 'whisper' or 'parakeet'
    is_enabled: bool
    sort_order: int

    @classmethod
    def from_row(cls, row) -> "RunPodModel":
        """Create from database row."""
        return cls(
            id=row[0],
            display_name=row[1],
            backend=row[2],
            is_enabled=bool(row[3]),
            sort_order=row[4],
        )


class RunPodModelRepository:
    """Repository for RunPod transcription model configurations."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def get_all(self, enabled_only: bool = True) -> list[RunPodModel]:
        """Get all models, ordered by sort_order."""
        if enabled_only:
            cursor = execute(
                self.conn,
                "SELECT id, display_name, backend, is_enabled, sort_order FROM runpod_models WHERE is_enabled = TRUE ORDER BY sort_order, id"
            )
        else:
            cursor = execute(
                self.conn,
                "SELECT id, display_name, backend, is_enabled, sort_order FROM runpod_models ORDER BY sort_order, id"
            )
        return [RunPodModel.from_row(row) for row in cursor.fetchall()]

    def get_by_id(self, model_id: str) -> Optional[RunPodModel]:
        """Get a model by ID."""
        cursor = execute(
            self.conn,
            "SELECT id, display_name, backend, is_enabled, sort_order FROM runpod_models WHERE id = %s",
            (model_id,),
        )
        row = cursor.fetchone()
        return RunPodModel.from_row(row) if row else None

    def upsert(
        self,
        model_id: str,
        display_name: str,
        backend: str = "whisper",
        is_enabled: bool = True,
        sort_order: int = 100,
    ) -> None:
        """Insert or update a model."""
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            INSERT INTO runpod_models (id, display_name, backend, is_enabled, sort_order, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                display_name = EXCLUDED.display_name, backend = EXCLUDED.backend,
                is_enabled = EXCLUDED.is_enabled, sort_order = EXCLUDED.sort_order
            """,
            (model_id, display_name, backend, is_enabled, sort_order, now),
        )
        self.conn.commit()

    def delete(self, model_id: str) -> bool:
        """Delete a model."""
        cursor = execute(self.conn, "DELETE FROM runpod_models WHERE id = %s", (model_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def seed_defaults(self) -> int:
        """Seed the default models if table is empty."""
        cursor = execute(self.conn, "SELECT COUNT(*) FROM runpod_models")
        if cursor.fetchone()[0] > 0:
            return 0

        # Import here to avoid circular import
        from cast2md.config.settings import RUNPOD_TRANSCRIPTION_MODELS

        now = datetime.now().isoformat()
        for idx, (model_id, display_name) in enumerate(RUNPOD_TRANSCRIPTION_MODELS):
            # Determine backend from model_id
            backend = "parakeet" if "parakeet" in model_id else "whisper"
            execute(
                self.conn,
                """
                INSERT INTO runpod_models (id, display_name, backend, is_enabled, sort_order, created_at)
                VALUES (%s, %s, %s, TRUE, %s, %s)
                """,
                (model_id, display_name, backend, idx * 10, now),
            )
        self.conn.commit()
        return len(RUNPOD_TRANSCRIPTION_MODELS)


class TranscriberNodeRepository:
    """Repository for transcriber node operations."""

    NODE_COLUMNS = """id, name, url, api_key, whisper_model, whisper_backend,
                      status, last_heartbeat, current_job_id, priority,
                      created_at, updated_at"""

    def __init__(self, conn: Connection):
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
        execute(
            self.conn,
            """
            INSERT INTO transcriber_node (
                id, name, url, api_key, whisper_model, whisper_backend,
                status, priority, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (node_id, name, url, api_key, whisper_model, whisper_backend,
             NodeStatus.OFFLINE.value, priority, now, now),
        )
        self.conn.commit()
        return self.get_by_id(node_id)

    def get_by_id(self, node_id: str) -> Optional[TranscriberNode]:
        """Get node by ID."""
        cursor = execute(
            self.conn,
            f"SELECT {self.NODE_COLUMNS} FROM transcriber_node WHERE id = %s",
            (node_id,),
        )
        row = cursor.fetchone()
        return TranscriberNode.from_row(row) if row else None

    def get_by_api_key(self, api_key: str) -> Optional[TranscriberNode]:
        """Get node by API key."""
        cursor = execute(
            self.conn,
            f"SELECT {self.NODE_COLUMNS} FROM transcriber_node WHERE api_key = %s",
            (api_key,),
        )
        row = cursor.fetchone()
        return TranscriberNode.from_row(row) if row else None

    def get_all(self) -> list[TranscriberNode]:
        """Get all nodes."""
        cursor = execute(
            self.conn,
            f"SELECT {self.NODE_COLUMNS} FROM transcriber_node ORDER BY priority, name"
        )
        return [TranscriberNode.from_row(row) for row in cursor.fetchall()]

    def get_online(self) -> list[TranscriberNode]:
        """Get all online nodes."""
        cursor = execute(
            self.conn,
            f"""
            SELECT {self.NODE_COLUMNS} FROM transcriber_node
            WHERE status IN (%s, %s)
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
        execute(
            self.conn,
            """
            UPDATE transcriber_node
            SET status = %s, current_job_id = %s, updated_at = %s
            WHERE id = %s
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
        execute(
            self.conn,
            """
            UPDATE transcriber_node
            SET last_heartbeat = %s, updated_at = %s
            WHERE id = %s
            """,
            (ts, now, node_id),
        )
        self.conn.commit()

    def update_info(
        self,
        node_id: str,
        name: str | None = None,
        whisper_model: str | None = None,
        whisper_backend: str | None = None,
    ) -> None:
        """Update node info (name, whisper model/backend)."""
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            UPDATE transcriber_node
            SET name = %s, whisper_model = %s, whisper_backend = %s, updated_at = %s
            WHERE id = %s
            """,
            (name, whisper_model, whisper_backend, now, node_id),
        )
        self.conn.commit()

    def delete(self, node_id: str) -> bool:
        """Delete a node."""
        cursor = execute(
            self.conn,
            "DELETE FROM transcriber_node WHERE id = %s",
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
        cursor = execute(
            self.conn,
            f"""
            SELECT {self.NODE_COLUMNS} FROM transcriber_node
            WHERE status != %s
            AND (last_heartbeat IS NULL OR last_heartbeat < %s)
            """,
            (NodeStatus.OFFLINE.value, threshold),
        )
        return [TranscriberNode.from_row(row) for row in cursor.fetchall()]

    def mark_offline(self, node_id: str) -> None:
        """Mark a node as offline and clear its current job."""
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            UPDATE transcriber_node
            SET status = %s, current_job_id = NULL, updated_at = %s
            WHERE id = %s
            """,
            (NodeStatus.OFFLINE.value, now, node_id),
        )
        self.conn.commit()

    def count_by_status(self) -> dict[str, int]:
        """Count nodes by status."""
        cursor = execute(
            self.conn,
            """
            SELECT status, COUNT(*) FROM transcriber_node
            GROUP BY status
            """
        )
        return dict(cursor.fetchall())

    def get_by_name(self, name: str) -> Optional[TranscriberNode]:
        """Get node by name."""
        cursor = execute(
            self.conn,
            f"SELECT {self.NODE_COLUMNS} FROM transcriber_node WHERE name = %s",
            (name,),
        )
        row = cursor.fetchone()
        return TranscriberNode.from_row(row) if row else None

    def delete_by_name(self, name: str) -> bool:
        """Delete a node by exact name.

        Args:
            name: The node name to delete.

        Returns:
            True if a node was deleted.
        """
        cursor = execute(
            self.conn,
            "DELETE FROM transcriber_node WHERE name = %s",
            (name,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def cleanup_stale_nodes(self, offline_hours: int = 24) -> int:
        """Delete nodes that have been offline for longer than threshold.

        Args:
            offline_hours: Hours a node must be offline before deletion.

        Returns:
            Number of nodes deleted.
        """
        threshold = (datetime.now() - timedelta(hours=offline_hours)).isoformat()

        cursor = execute(
            self.conn,
            """
            DELETE FROM transcriber_node
            WHERE status = %s
              AND (last_heartbeat IS NULL OR last_heartbeat < %s)
              AND current_job_id IS NULL
            """,
            (NodeStatus.OFFLINE.value, threshold),
        )
        self.conn.commit()
        return cursor.rowcount

    def get_stale_offline_nodes(self, offline_hours: int = 24) -> list[TranscriberNode]:
        """Get nodes that have been offline for longer than threshold.

        Args:
            offline_hours: Hours a node must be offline to be considered stale.

        Returns:
            List of stale offline nodes.
        """
        threshold = (datetime.now() - timedelta(hours=offline_hours)).isoformat()

        cursor = execute(
            self.conn,
            f"""
            SELECT {self.NODE_COLUMNS} FROM transcriber_node
            WHERE status = %s
              AND (last_heartbeat IS NULL OR last_heartbeat < %s)
            ORDER BY last_heartbeat ASC NULLS FIRST
            """,
            (NodeStatus.OFFLINE.value, threshold),
        )
        return [TranscriberNode.from_row(row) for row in cursor.fetchall()]


class PodRunRepository:
    """Repository for RunPod pod run history."""

    def __init__(self, conn: Any):
        self.conn = conn

    def create(
        self,
        instance_id: str,
        pod_id: str | None,
        pod_name: str,
        gpu_type: str,
        gpu_price_hr: float | None,
        started_at: datetime,
    ) -> int:
        """Create a new pod run record. Returns the ID."""
        cursor = execute(
            self.conn,
            """
            INSERT INTO pod_runs (instance_id, pod_id, pod_name, gpu_type, gpu_price_hr, started_at, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'running')
            RETURNING id
            """,
            (instance_id, pod_id, pod_name, gpu_type, gpu_price_hr, started_at.isoformat()),
        )
        self.conn.commit()
        row = cursor.fetchone()
        return row[0] if row else 0

    def end_run(self, pod_id: str, jobs_completed: int = 0) -> None:
        """Mark a pod run as ended."""
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            UPDATE pod_runs
            SET ended_at = %s, jobs_completed = %s, status = 'completed'
            WHERE pod_id = %s AND status = 'running'
            """,
            (now, jobs_completed, pod_id),
        )
        self.conn.commit()

    def get_recent(self, limit: int = 20) -> list[dict]:
        """Get recent pod runs with computed cost."""
        cursor = execute(
            self.conn,
            """
            SELECT
                id, instance_id, pod_id, pod_name, gpu_type, gpu_price_hr,
                started_at, ended_at, jobs_completed, status,
                CASE
                    WHEN ended_at IS NOT NULL AND gpu_price_hr IS NOT NULL THEN
                        ROUND((EXTRACT(EPOCH FROM (ended_at - started_at)) / 3600 * gpu_price_hr)::numeric, 2)
                    ELSE NULL
                END as cost
            FROM pod_runs
            ORDER BY started_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_stats(self, days: int = 30) -> dict:
        """Get aggregate stats for pod runs."""
        cursor = execute(
            self.conn,
            """
            SELECT
                COUNT(*) as total_runs,
                COALESCE(SUM(jobs_completed), 0) as total_jobs,
                ROUND(COALESCE(SUM(
                    CASE WHEN ended_at IS NOT NULL AND gpu_price_hr IS NOT NULL THEN
                        EXTRACT(EPOCH FROM (ended_at - started_at)) / 3600 * gpu_price_hr
                    ELSE 0 END
                ), 0)::numeric, 2) as total_cost,
                ROUND(COALESCE(SUM(
                    CASE WHEN ended_at IS NOT NULL THEN
                        EXTRACT(EPOCH FROM (ended_at - started_at)) / 3600
                    ELSE 0 END
                ), 0)::numeric, 1) as total_hours
            FROM pod_runs
            WHERE started_at > NOW() - INTERVAL '%s days'
            """,
            (days,),
        )
        row = cursor.fetchone()
        if row:
            return {
                "total_runs": row[0],
                "total_jobs": row[1],
                "total_cost": float(row[2]) if row[2] else 0,
                "total_hours": float(row[3]) if row[3] else 0,
            }
        return {"total_runs": 0, "total_jobs": 0, "total_cost": 0, "total_hours": 0}

    def mark_orphaned_as_ended(self, active_pod_ids: set[str]) -> int:
        """Mark running pod runs as ended if their pod is no longer active.

        Returns count of runs marked as ended.
        """
        if not active_pod_ids:
            # No active pods - mark all running as ended
            cursor = execute(
                self.conn,
                """
                UPDATE pod_runs
                SET ended_at = NOW(), status = 'completed'
                WHERE status = 'running'
                """,
            )
        else:
            # Mark those not in active list
            cursor = execute(
                self.conn,
                """
                UPDATE pod_runs
                SET ended_at = NOW(), status = 'completed'
                WHERE status = 'running' AND pod_id NOT IN %s
                """,
                (tuple(active_pod_ids),),
            )
        self.conn.commit()
        return cursor.rowcount


@dataclass
class PodSetupStateRow:
    """Database representation of a pod setup state."""

    instance_id: str
    pod_id: str | None
    pod_name: str
    ts_hostname: str
    node_name: str
    gpu_type: str
    phase: str
    message: str
    started_at: datetime
    error: str | None
    host_ip: str | None
    persistent: bool
    setup_token: str = ""

    @classmethod
    def from_row(cls, row: tuple) -> "PodSetupStateRow":
        return cls(
            instance_id=row[0],
            pod_id=row[1],
            pod_name=row[2],
            ts_hostname=row[3],
            node_name=row[4],
            gpu_type=row[5] or "",
            phase=row[6],
            message=row[7] or "",
            started_at=row[8] if isinstance(row[8], datetime) else datetime.fromisoformat(row[8]),
            error=row[9],
            host_ip=row[10],
            persistent=row[11] if row[11] is not None else False,
            setup_token=row[12] if len(row) > 12 else "",
        )


class PodSetupStateRepository:
    """Repository for persistent pod setup states."""

    COLUMNS = """instance_id, pod_id, pod_name, ts_hostname, node_name, gpu_type,
                 phase, message, started_at, error, host_ip, persistent, setup_token"""

    def __init__(self, conn: Any):
        self.conn = conn

    def upsert(self, state: PodSetupStateRow) -> None:
        """Insert or update a pod setup state."""
        now = datetime.now().isoformat()
        execute(
            self.conn,
            f"""
            INSERT INTO pod_setup_states ({self.COLUMNS}, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (instance_id) DO UPDATE SET
                pod_id = EXCLUDED.pod_id,
                pod_name = EXCLUDED.pod_name,
                ts_hostname = EXCLUDED.ts_hostname,
                node_name = EXCLUDED.node_name,
                gpu_type = EXCLUDED.gpu_type,
                phase = EXCLUDED.phase,
                message = EXCLUDED.message,
                error = EXCLUDED.error,
                host_ip = EXCLUDED.host_ip,
                persistent = EXCLUDED.persistent,
                setup_token = EXCLUDED.setup_token,
                updated_at = EXCLUDED.updated_at
            """,
            (
                state.instance_id,
                state.pod_id,
                state.pod_name,
                state.ts_hostname,
                state.node_name,
                state.gpu_type,
                state.phase,
                state.message,
                state.started_at.isoformat(),
                state.error,
                state.host_ip,
                state.persistent,
                state.setup_token,
                now,
                now,
            ),
        )
        self.conn.commit()

    def get(self, instance_id: str) -> PodSetupStateRow | None:
        """Get a pod setup state by instance ID."""
        cursor = execute(
            self.conn,
            f"SELECT {self.COLUMNS} FROM pod_setup_states WHERE instance_id = %s",
            (instance_id,),
        )
        row = cursor.fetchone()
        return PodSetupStateRow.from_row(row) if row else None

    def get_all(self) -> list[PodSetupStateRow]:
        """Get all pod setup states."""
        cursor = execute(
            self.conn,
            f"SELECT {self.COLUMNS} FROM pod_setup_states ORDER BY started_at DESC",
        )
        return [PodSetupStateRow.from_row(row) for row in cursor.fetchall()]

    def delete(self, instance_id: str) -> bool:
        """Delete a pod setup state."""
        cursor = execute(
            self.conn,
            "DELETE FROM pod_setup_states WHERE instance_id = %s",
            (instance_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def cleanup_old(self, hours: int = 24) -> int:
        """Delete setup states older than the specified hours.

        Only deletes states that are in 'ready' or 'failed' phase.
        """
        threshold = (datetime.now() - timedelta(hours=hours)).isoformat()
        cursor = execute(
            self.conn,
            """
            DELETE FROM pod_setup_states
            WHERE started_at < %s
            AND phase IN ('ready', 'failed')
            AND persistent = FALSE
            """,
            (threshold,),
        )
        self.conn.commit()
        return cursor.rowcount

    def set_persistent(self, instance_id: str, persistent: bool) -> bool:
        """Set the persistent flag for a pod setup state."""
        cursor = execute(
            self.conn,
            """
            UPDATE pod_setup_states
            SET persistent = %s, updated_at = %s
            WHERE instance_id = %s
            """,
            (persistent, datetime.now().isoformat(), instance_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0
