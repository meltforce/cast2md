"""Repository classes for database operations."""

import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from cast2md.db.models import Episode, EpisodeStatus, Feed, Job, JobStatus, JobType


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
        now = datetime.utcnow().isoformat()

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

    def get_next_job(self, job_type: JobType) -> Optional[Job]:
        """Get the next queued job of given type, ordered by priority.

        Also respects next_retry_at for failed jobs being retried.
        """
        now = datetime.utcnow().isoformat()
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
        now = datetime.utcnow().isoformat()
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

    def mark_running(self, job_id: int) -> None:
        """Mark a job as running."""
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """
            UPDATE job_queue
            SET status = ?, started_at = ?, attempts = attempts + 1
            WHERE id = ?
            """,
            (JobStatus.RUNNING.value, now, job_id),
        )
        self.conn.commit()

    def mark_completed(self, job_id: int) -> None:
        """Mark a job as completed."""
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """
            UPDATE job_queue
            SET status = ?, completed_at = ?
            WHERE id = ?
            """,
            (JobStatus.COMPLETED.value, now, job_id),
        )
        self.conn.commit()

    def mark_failed(self, job_id: int, error_message: str, retry: bool = True) -> None:
        """Mark a job as failed, optionally scheduling a retry."""
        now = datetime.utcnow()

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
        cutoff = (datetime.utcnow() - timedelta(days=older_than_days)).isoformat()

        cursor = self.conn.execute(
            """
            DELETE FROM job_queue
            WHERE status IN (?, ?) AND completed_at < ?
            """,
            (JobStatus.COMPLETED.value, JobStatus.FAILED.value, cutoff),
        )
        self.conn.commit()
        return cursor.rowcount
