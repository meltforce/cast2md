"""Tests for JobRepository state transitions and retry logic."""

from datetime import datetime, timedelta

from cast2md.db.models import EpisodeStatus, JobStatus, JobType


class TestJobCreation:
    """Tests for job creation."""

    def test_create_job(self, job_repo, sample_episode):
        """Test creating a new job."""
        job = job_repo.create(
            episode_id=sample_episode.id,
            job_type=JobType.DOWNLOAD,
            priority=5,
            max_attempts=3,
        )

        assert job.id is not None
        assert job.episode_id == sample_episode.id
        assert job.job_type == JobType.DOWNLOAD
        assert job.priority == 5
        assert job.max_attempts == 3
        assert job.attempts == 0
        assert job.status == JobStatus.QUEUED

    def test_create_transcribe_job(self, job_repo, sample_episode):
        """Test creating a transcription job."""
        job = job_repo.create(
            episode_id=sample_episode.id,
            job_type=JobType.TRANSCRIBE,
            priority=10,
            max_attempts=2,
        )

        assert job.job_type == JobType.TRANSCRIBE
        assert job.max_attempts == 2


class TestJobStateTransitions:
    """Tests for job state transitions."""

    def test_mark_running(self, job_repo, sample_job):
        """Test marking a job as running."""
        job_repo.mark_running(sample_job.id)

        job = job_repo.get_by_id(sample_job.id)
        assert job.status == JobStatus.RUNNING
        assert job.attempts == 1
        assert job.started_at is not None

    def test_mark_completed(self, job_repo, sample_job):
        """Test marking a job as completed."""
        job_repo.mark_running(sample_job.id)
        job_repo.mark_completed(sample_job.id)

        job = job_repo.get_by_id(sample_job.id)
        assert job.status == JobStatus.COMPLETED
        assert job.completed_at is not None
        assert job.progress_percent == 100

    def test_mark_failed_with_retry(self, job_repo, sample_job):
        """Test marking a job as failed with retry enabled."""
        job_repo.mark_running(sample_job.id)
        job_repo.mark_failed(sample_job.id, error_message="Test error", retry=True)

        job = job_repo.get_by_id(sample_job.id)
        # Should be requeued since attempts (1) < max_attempts (3)
        assert job.status == JobStatus.QUEUED
        assert job.error_message == "Test error"
        assert job.next_retry_at is not None

    def test_mark_failed_max_attempts_reached(self, job_repo, sample_episode):
        """Test that job fails permanently when max attempts reached."""
        job = job_repo.create(
            episode_id=sample_episode.id,
            job_type=JobType.DOWNLOAD,
            max_attempts=2,
        )

        # Run twice to reach max attempts
        job_repo.mark_running(job.id)
        job_repo.mark_failed(job.id, "Error 1", retry=True)

        job_repo.mark_running(job.id)
        job_repo.mark_failed(job.id, "Error 2", retry=True)

        job = job_repo.get_by_id(job.id)
        assert job.status == JobStatus.FAILED
        assert job.attempts == 2


class TestReclaimStaleJobs:
    """Tests for reclaim_stale_jobs to verify the retry limit fix."""

    def test_reclaim_stale_job_with_retries_remaining(self, db_conn, job_repo, sample_episode):
        """Test that stale jobs with retries remaining are requeued."""
        job = job_repo.create(
            episode_id=sample_episode.id,
            job_type=JobType.TRANSCRIBE,
            max_attempts=3,
        )

        # Simulate a job that started and got stuck
        old_time = (datetime.utcnow() - timedelta(hours=3)).isoformat()
        db_conn.execute(
            """
            UPDATE job_queue
            SET status = ?, started_at = ?, claimed_at = ?,
                assigned_node_id = 'node-1', attempts = 1
            WHERE id = ?
            """,
            (JobStatus.RUNNING.value, old_time, old_time, job.id),
        )
        db_conn.commit()

        # Reclaim with 2 hour timeout
        requeued, failed = job_repo.reclaim_stale_jobs(timeout_hours=2)

        assert requeued == 1
        assert failed == 0

        job = job_repo.get_by_id(job.id)
        assert job.status == JobStatus.QUEUED
        assert job.assigned_node_id is None

    def test_reclaim_stale_job_max_attempts_exceeded(self, db_conn, job_repo, sample_episode):
        """Test that stale jobs exceeding max attempts are failed permanently."""
        job = job_repo.create(
            episode_id=sample_episode.id,
            job_type=JobType.TRANSCRIBE,
            max_attempts=3,
        )

        # Simulate a job that has already been attempted 3+ times
        old_time = (datetime.utcnow() - timedelta(hours=3)).isoformat()
        db_conn.execute(
            """
            UPDATE job_queue
            SET status = ?, started_at = ?, claimed_at = ?,
                assigned_node_id = 'node-1', attempts = 3
            WHERE id = ?
            """,
            (JobStatus.RUNNING.value, old_time, old_time, job.id),
        )
        db_conn.commit()

        # Reclaim with 2 hour timeout
        requeued, failed = job_repo.reclaim_stale_jobs(timeout_hours=2)

        assert requeued == 0
        assert failed == 1

        job = job_repo.get_by_id(job.id)
        assert job.status == JobStatus.FAILED
        assert "Max attempts exceeded" in job.error_message
        assert job.completed_at is not None

    def test_reclaim_mixed_jobs(self, db_conn, job_repo, sample_feed, episode_repo):
        """Test reclaiming a mix of retriable and exhausted jobs."""
        # Create two episodes
        ep1 = episode_repo.create(
            feed_id=sample_feed.id,
            guid="ep-1",
            title="Episode 1",
            audio_url="https://example.com/1.mp3",
        )
        ep2 = episode_repo.create(
            feed_id=sample_feed.id,
            guid="ep-2",
            title="Episode 2",
            audio_url="https://example.com/2.mp3",
        )

        # Create jobs
        job1 = job_repo.create(episode_id=ep1.id, job_type=JobType.TRANSCRIBE, max_attempts=3)
        job2 = job_repo.create(episode_id=ep2.id, job_type=JobType.TRANSCRIBE, max_attempts=3)

        # Make both jobs stale, but with different attempt counts
        old_time = (datetime.utcnow() - timedelta(hours=3)).isoformat()

        # Job 1: only 1 attempt, should be requeued
        db_conn.execute(
            """
            UPDATE job_queue
            SET status = ?, started_at = ?, claimed_at = ?,
                assigned_node_id = 'node-1', attempts = 1
            WHERE id = ?
            """,
            (JobStatus.RUNNING.value, old_time, old_time, job1.id),
        )

        # Job 2: 5 attempts (exceeded max of 3), should fail
        db_conn.execute(
            """
            UPDATE job_queue
            SET status = ?, started_at = ?, claimed_at = ?,
                assigned_node_id = 'node-1', attempts = 5
            WHERE id = ?
            """,
            (JobStatus.RUNNING.value, old_time, old_time, job2.id),
        )
        db_conn.commit()

        requeued, failed = job_repo.reclaim_stale_jobs(timeout_hours=2)

        assert requeued == 1
        assert failed == 1

        job1 = job_repo.get_by_id(job1.id)
        job2 = job_repo.get_by_id(job2.id)

        assert job1.status == JobStatus.QUEUED
        assert job2.status == JobStatus.FAILED

    def test_no_infinite_retry_loop(self, db_conn, job_repo, sample_episode):
        """Regression test: Verify jobs cannot exceed max_attempts through reclaiming.

        This test reproduces the "19/3 attempts" bug where reclaim_stale_jobs
        would reset jobs without checking the attempt count.
        """
        job = job_repo.create(
            episode_id=sample_episode.id,
            job_type=JobType.TRANSCRIBE,
            max_attempts=3,
        )

        # Simulate the bug scenario: job has been reclaimed many times
        # and has 19 attempts but is still running
        old_time = (datetime.utcnow() - timedelta(hours=3)).isoformat()
        db_conn.execute(
            """
            UPDATE job_queue
            SET status = ?, started_at = ?, claimed_at = ?,
                assigned_node_id = 'node-1', attempts = 19
            WHERE id = ?
            """,
            (JobStatus.RUNNING.value, old_time, old_time, job.id),
        )
        db_conn.commit()

        # Reclaim should fail this job, not requeue it
        requeued, failed = job_repo.reclaim_stale_jobs(timeout_hours=2)

        assert requeued == 0
        assert failed == 1

        job = job_repo.get_by_id(job.id)
        assert job.status == JobStatus.FAILED
        assert job.attempts == 19  # Attempts unchanged


class TestResetRunningJobs:
    """Tests for reset_running_jobs (server startup recovery)."""

    def test_reset_running_jobs_with_retries(self, db_conn, job_repo, sample_episode):
        """Test that running jobs with retries are requeued on startup."""
        job = job_repo.create(
            episode_id=sample_episode.id,
            job_type=JobType.DOWNLOAD,
            max_attempts=3,
        )

        # Simulate running job with 1 attempt
        db_conn.execute(
            """
            UPDATE job_queue
            SET status = ?, started_at = ?, attempts = 1
            WHERE id = ?
            """,
            (JobStatus.RUNNING.value, datetime.utcnow().isoformat(), job.id),
        )
        db_conn.commit()

        requeued, failed = job_repo.reset_running_jobs()

        assert requeued == 1
        assert failed == 0

        job = job_repo.get_by_id(job.id)
        assert job.status == JobStatus.QUEUED

    def test_reset_running_jobs_max_attempts(self, db_conn, job_repo, sample_episode, episode_repo):
        """Test that running jobs at max attempts are failed on startup."""
        job = job_repo.create(
            episode_id=sample_episode.id,
            job_type=JobType.DOWNLOAD,
            max_attempts=3,
        )

        # Simulate running job at max attempts
        db_conn.execute(
            """
            UPDATE job_queue
            SET status = ?, started_at = ?, attempts = 3
            WHERE id = ?
            """,
            (JobStatus.RUNNING.value, datetime.utcnow().isoformat(), job.id),
        )
        db_conn.commit()

        requeued, failed = job_repo.reset_running_jobs()

        assert requeued == 0
        assert failed == 1

        job = job_repo.get_by_id(job.id)
        assert job.status == JobStatus.FAILED

        # Check episode status was also updated
        episode = episode_repo.get_by_id(sample_episode.id)
        assert episode.status == EpisodeStatus.FAILED


class TestBatchForceResetStuck:
    """Tests for batch_force_reset_stuck."""

    def test_batch_reset_respects_max_attempts(self, db_conn, job_repo, sample_episode):
        """Test that batch reset respects max attempts."""
        job = job_repo.create(
            episode_id=sample_episode.id,
            job_type=JobType.TRANSCRIBE,
            max_attempts=2,
        )

        # Make job stuck with max attempts
        old_time = (datetime.utcnow() - timedelta(hours=5)).isoformat()
        db_conn.execute(
            """
            UPDATE job_queue
            SET status = ?, started_at = ?, attempts = 2
            WHERE id = ?
            """,
            (JobStatus.RUNNING.value, old_time, job.id),
        )
        db_conn.commit()

        requeued, failed = job_repo.batch_force_reset_stuck(threshold_hours=2)

        assert requeued == 0
        assert failed == 1

        job = job_repo.get_by_id(job.id)
        assert job.status == JobStatus.FAILED


class TestJobClaiming:
    """Tests for distributed job claiming."""

    def test_claim_job(self, job_repo, sample_job):
        """Test claiming a job for a node."""
        job_repo.claim_job(sample_job.id, "node-123")

        job = job_repo.get_by_id(sample_job.id)
        assert job.status == JobStatus.RUNNING
        assert job.assigned_node_id == "node-123"
        assert job.claimed_at is not None
        assert job.attempts == 1

    def test_unclaim_job(self, job_repo, sample_job):
        """Test unclaiming a job."""
        job_repo.claim_job(sample_job.id, "node-123")
        job_repo.unclaim_job(sample_job.id)

        job = job_repo.get_by_id(sample_job.id)
        assert job.assigned_node_id is None
        assert job.claimed_at is None

    def test_get_jobs_by_node(self, job_repo, sample_job):
        """Test getting jobs assigned to a specific node."""
        job_repo.claim_job(sample_job.id, "node-123")

        jobs = job_repo.get_jobs_by_node("node-123")
        assert len(jobs) == 1
        assert jobs[0].id == sample_job.id


class TestJobQueries:
    """Tests for job query methods."""

    def test_get_queued_jobs(self, job_repo, sample_job):
        """Test getting queued jobs."""
        jobs = job_repo.get_queued_jobs(job_type=JobType.DOWNLOAD)
        assert len(jobs) == 1
        assert jobs[0].id == sample_job.id

    def test_get_running_jobs(self, job_repo, sample_job):
        """Test getting running jobs."""
        job_repo.mark_running(sample_job.id)

        jobs = job_repo.get_running_jobs(JobType.DOWNLOAD)
        assert len(jobs) == 1
        assert jobs[0].status == JobStatus.RUNNING

    def test_has_pending_job(self, job_repo, sample_job, sample_episode):
        """Test checking for pending jobs."""
        assert job_repo.has_pending_job(sample_episode.id, JobType.DOWNLOAD)
        assert not job_repo.has_pending_job(sample_episode.id, JobType.TRANSCRIBE)

    def test_get_failed_jobs(self, job_repo, sample_job):
        """Test getting failed jobs."""
        job_repo.mark_running(sample_job.id)
        job_repo.mark_failed(sample_job.id, "Error", retry=False)

        jobs = job_repo.get_failed_jobs()
        assert len(jobs) == 1
        assert jobs[0].status == JobStatus.FAILED


class TestJobRetry:
    """Tests for job retry functionality."""

    def test_retry_failed_job(self, job_repo, sample_job):
        """Test retrying a failed job."""
        job_repo.mark_running(sample_job.id)
        job_repo.mark_failed(sample_job.id, "Error", retry=False)

        success = job_repo.retry_failed_job(sample_job.id)
        assert success

        job = job_repo.get_by_id(sample_job.id)
        assert job.status == JobStatus.QUEUED
        assert job.attempts == 0
        assert job.error_message is None

    def test_retry_non_failed_job(self, job_repo, sample_job):
        """Test that retry_failed_job only works on failed jobs."""
        success = job_repo.retry_failed_job(sample_job.id)
        assert not success

        job = job_repo.get_by_id(sample_job.id)
        assert job.status == JobStatus.QUEUED  # Unchanged


class TestMarkRunningLocalNode:
    """Tests for mark_running with local node tracking."""

    def test_mark_running_sets_local_node_id(self, job_repo, sample_job):
        """Test that mark_running sets assigned_node_id to 'local' by default."""
        job_repo.mark_running(sample_job.id)

        job = job_repo.get_by_id(sample_job.id)
        assert job.assigned_node_id == "local"
        assert job.claimed_at is not None

    def test_mark_running_sets_custom_node_id(self, job_repo, sample_job):
        """Test that mark_running can set a custom node_id."""
        job_repo.mark_running(sample_job.id, node_id="custom-node")

        job = job_repo.get_by_id(sample_job.id)
        assert job.assigned_node_id == "custom-node"
        assert job.claimed_at is not None

    def test_local_job_reclaimed_when_stuck(self, db_conn, job_repo, sample_episode):
        """Test that local jobs (with assigned_node_id='local') are reclaimed when stuck."""
        job = job_repo.create(
            episode_id=sample_episode.id,
            job_type=JobType.TRANSCRIBE,
            max_attempts=3,
        )
        job_repo.mark_running(job.id)  # Sets assigned_node_id='local'

        # Simulate stuck for 3 hours
        old_time = (datetime.utcnow() - timedelta(hours=3)).isoformat()
        db_conn.execute(
            "UPDATE job_queue SET started_at = ? WHERE id = ?",
            (old_time, job.id),
        )
        db_conn.commit()

        requeued, failed = job_repo.reclaim_stale_jobs(timeout_hours=2)
        assert requeued == 1

        job = job_repo.get_by_id(job.id)
        assert job.status == JobStatus.QUEUED


class TestReclaimUsesStartedAt:
    """Tests for reclaim_stale_jobs using started_at instead of claimed_at."""

    def test_reclaim_uses_started_at_not_claimed_at(self, db_conn, job_repo, sample_episode):
        """Test that reclaim checks started_at, not claimed_at.

        This prevents the claim/fail cycle from resetting the timeout.
        """
        job = job_repo.create(
            episode_id=sample_episode.id,
            job_type=JobType.TRANSCRIBE,
            max_attempts=3,
        )

        # started_at is old (3 hours ago), but claimed_at is recent
        # This simulates a job that was reclaimed recently but actually started long ago
        old_started = (datetime.utcnow() - timedelta(hours=3)).isoformat()
        recent_claimed = datetime.utcnow().isoformat()
        db_conn.execute(
            """
            UPDATE job_queue
            SET status = ?, started_at = ?, claimed_at = ?,
                assigned_node_id = 'node-1', attempts = 1
            WHERE id = ?
            """,
            (JobStatus.RUNNING.value, old_started, recent_claimed, job.id),
        )
        db_conn.commit()

        # Should still be reclaimed based on started_at, not claimed_at
        requeued, _ = job_repo.reclaim_stale_jobs(timeout_hours=2)
        assert requeued == 1

        job = job_repo.get_by_id(job.id)
        assert job.status == JobStatus.QUEUED

    def test_recent_started_at_not_reclaimed(self, db_conn, job_repo, sample_episode):
        """Test that jobs with recent started_at are not reclaimed."""
        job = job_repo.create(
            episode_id=sample_episode.id,
            job_type=JobType.TRANSCRIBE,
            max_attempts=3,
        )

        # started_at is recent (1 hour ago), within timeout
        recent_started = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        db_conn.execute(
            """
            UPDATE job_queue
            SET status = ?, started_at = ?, claimed_at = ?,
                assigned_node_id = 'node-1', attempts = 1
            WHERE id = ?
            """,
            (JobStatus.RUNNING.value, recent_started, recent_started, job.id),
        )
        db_conn.commit()

        # Should NOT be reclaimed - started_at is within 2-hour timeout
        requeued, failed = job_repo.reclaim_stale_jobs(timeout_hours=2)
        assert requeued == 0
        assert failed == 0

        job = job_repo.get_by_id(job.id)
        assert job.status == JobStatus.RUNNING
