"""Repository for podcast episodes."""

from datetime import datetime, timedelta
from typing import Any

from cast2md.db.models import Episode, EpisodeStatus
from cast2md.db.sql import Connection, execute
from cast2md.db.tsquery import build_flexible_tsquery


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
                feed_id,
                guid,
                title,
                description,
                audio_url,
                duration_seconds,
                published_str,
                EpisodeStatus.NEW.value,
                transcript_url,
                transcript_type,
                link,
                author,
                now,
                now,
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

    def get_by_id(self, episode_id: int) -> Episode | None:
        """Get episode by ID."""
        cursor = execute(
            self.conn,
            f"SELECT {self.EPISODE_COLUMNS} FROM episode WHERE id = %s",
            (episode_id,),
        )
        row = cursor.fetchone()
        return Episode.from_row(row) if row else None

    def get_by_guid(self, feed_id: int, guid: str) -> Episode | None:
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

    def get_transcript_paths(self, feed_id: int | None = None) -> dict[int, str]:
        """Map episode ID to transcript path for completed episodes with a transcript.

        Args:
            feed_id: Restrict to one feed, or None for every feed.

        Returns:
            Dict of episode ID to transcript path, in the shape the search
            repository's reindex_all expects.
        """
        sql = """
            SELECT id, transcript_path FROM episode
            WHERE transcript_path IS NOT NULL AND status = %s
        """
        params: list[Any] = [EpisodeStatus.COMPLETED.value]

        if feed_id is not None:
            sql += " AND feed_id = %s"
            params.append(feed_id)

        cursor = execute(self.conn, sql, tuple(params))
        return {row[0]: row[1] for row in cursor.fetchall()}

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

    # Sort orders accepted by get_by_status. Keys are the public API values,
    # values the SQL fragment — the mapping is what keeps the ORDER BY clause
    # free of caller-supplied text.
    STATUS_ORDERS = {
        "created_asc": "created_at ASC",
        "updated_asc": "updated_at ASC",
        "updated_desc": "updated_at DESC",
    }

    def get_by_status(
        self,
        status: EpisodeStatus,
        limit: int = 100,
        since: str | None = None,
        feed_id: int | None = None,
        order: str = "created_asc",
    ) -> list[Episode]:
        """Get episodes by status.

        Args:
            status: Episode status to filter on.
            limit: Maximum number of episodes to return.
            since: Return only episodes with updated_at strictly greater than
                this timestamp. The value is compared as a naive local
                timestamp, matching what update_status writes.
            feed_id: Restrict the result to a single feed.
            order: One of STATUS_ORDERS. Unknown values raise ValueError.
        """
        if order not in self.STATUS_ORDERS:
            raise ValueError(f"Invalid order: {order}. Valid options: {sorted(self.STATUS_ORDERS)}")

        clauses = ["status = %s"]
        params: list[Any] = [status.value]

        if since:
            clauses.append("updated_at > %s::timestamp")
            params.append(since)
        if feed_id is not None:
            clauses.append("feed_id = %s")
            params.append(feed_id)

        params.append(limit)
        cursor = execute(
            self.conn,
            f"""
            SELECT {self.EPISODE_COLUMNS} FROM episode
            WHERE {" AND ".join(clauses)}
            ORDER BY {self.STATUS_ORDERS[order]}
            LIMIT %s
            """,
            tuple(params),
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

    def get_retranscribable_episodes(self, feed_id: int, current_model: str) -> list[Episode]:
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

    def count_by_feed(self, feed_id: int, exclude_permanent_failures: bool = False) -> int:
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

    def count_by_status_per_feed(self) -> dict[int, dict[str, int]]:
        """Count episodes by status for every feed, in one query.

        Used by the feed list, which needs the breakdown for all feeds at once.
        Calling count_by_feed_and_status per feed and status would be dozens of
        round trips for the same information.

        Returns:
            {feed_id: {status: count}}. Statuses with no episodes are absent.
        """
        cursor = execute(
            self.conn,
            "SELECT feed_id, status, COUNT(*) FROM episode GROUP BY feed_id, status",
        )
        counts: dict[int, dict[str, int]] = {}
        for feed_id, status, count in cursor.fetchall():
            counts.setdefault(feed_id, {})[status] = count
        return counts

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
