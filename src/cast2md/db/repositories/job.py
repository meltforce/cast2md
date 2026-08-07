"""Repository for the background job queue."""

from datetime import datetime, timedelta

from cast2md.db.models import Job, JobStatus, JobType
from cast2md.db.sql import Connection, execute


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
                episode_id,
                job_type.value,
                priority,
                JobStatus.QUEUED.value,
                0,
                max_attempts,
                now,
                now,
            ),
        )
        job_id = cursor.fetchone()[0]

        self.conn.commit()
        return self.get_by_id(job_id)

    def get_by_id(self, job_id: int) -> Job | None:
        """Get job by ID."""
        cursor = execute(
            self.conn,
            "SELECT * FROM job_queue WHERE id = %s",
            (job_id,),
        )
        row = cursor.fetchone()
        return Job.from_row(row) if row else None

    def get_next_job(self, job_type: JobType, local_only: bool = False) -> Job | None:
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
    ) -> Job | None:
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
            (
                JobStatus.RUNNING.value,
                now,
                node_id,
                now,
                job_type.value,
                JobStatus.QUEUED.value,
                now,
            ),
        )

        row = cursor.fetchone()
        self.conn.commit()
        return Job.from_row(row) if row else None

    def get_next_unclaimed_job(self, job_type: JobType) -> Job | None:
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
            backoff_minutes = min(5**job.attempts, 720)
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

    def get_stuck_jobs(self, threshold_minutes: int) -> list[Job]:
        """Get jobs that have been running longer than threshold.

        Args:
            threshold_minutes: Minutes after which a running job is considered stuck.

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
    ) -> list[Job]:
        """Get all jobs with optional filters.

        Args:
            status: Filter by job status.
            job_type: Filter by job type.
            limit: Maximum number of jobs to return.

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

    def batch_force_reset_stuck(self, threshold_minutes: int) -> tuple[int, int]:
        """Reset all stuck jobs back to queued state or fail them if max attempts exceeded.

        Args:
            threshold_minutes: Minutes after which a running job is considered stuck.

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

    def count_stuck_jobs(self, threshold_minutes: int) -> int:
        """Count jobs that have been running longer than threshold.

        Args:
            threshold_minutes: Minutes after which a running job is considered stuck.

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
