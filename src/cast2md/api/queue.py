"""Queue management API endpoints.

Priority System:
- Lower number = higher priority (processed first)
- 1: High priority (auto-queued new episodes from feed discovery)
- 10: Default/normal priority (manually queued episodes)
- Jobs are processed in priority order, then by scheduled time (FIFO within same priority)
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cast2md.db.connection import get_db
from cast2md.db.models import JobStatus, JobType
from cast2md.db.repository import EpisodeRepository, JobRepository
from cast2md.worker import get_worker_manager

router = APIRouter(prefix="/api/queue", tags=["queue"])


class JobResponse(BaseModel):
    """Response model for a job."""

    id: int
    episode_id: int
    job_type: str
    priority: int
    status: str
    attempts: int
    max_attempts: int
    scheduled_at: str
    started_at: str | None
    completed_at: str | None
    next_retry_at: str | None
    error_message: str | None
    created_at: str


class JobListResponse(BaseModel):
    """Response model for job list."""

    jobs: list[JobResponse]


class JobInfo(BaseModel):
    """Brief job info with episode name."""

    job_id: int
    episode_id: int
    episode_title: str
    priority: int


class QueueDetails(BaseModel):
    """Queue details with counts and job lists."""

    queued: int
    running: int
    completed: int
    failed: int
    running_jobs: list[JobInfo]
    queued_jobs: list[JobInfo]


class QueueStatusResponse(BaseModel):
    """Response model for queue status."""

    running: bool
    download_workers: int
    transcribe_workers: int
    download_queue: QueueDetails
    transcribe_queue: QueueDetails


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str
    job_id: int | None = None


class QueueEpisodeRequest(BaseModel):
    """Request to queue an episode."""

    priority: int = 10


def _job_to_response(job) -> JobResponse:
    """Convert Job model to response."""
    return JobResponse(
        id=job.id,
        episode_id=job.episode_id,
        job_type=job.job_type.value,
        priority=job.priority,
        status=job.status.value,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        scheduled_at=job.scheduled_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        next_retry_at=job.next_retry_at.isoformat() if job.next_retry_at else None,
        error_message=job.error_message,
        created_at=job.created_at.isoformat(),
    )


@router.get("", response_model=JobListResponse)
def list_queue(job_type: str | None = None, limit: int = 100):
    """List queued jobs."""
    jt = None
    if job_type:
        try:
            jt = JobType(job_type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid job type: {job_type}")

    with get_db() as conn:
        repo = JobRepository(conn)
        jobs = repo.get_queued_jobs(jt, limit=limit)

    return JobListResponse(jobs=[_job_to_response(j) for j in jobs])


def _get_job_infos(jobs: list, episode_repo: EpisodeRepository) -> list[JobInfo]:
    """Convert jobs to JobInfo with episode names."""
    result = []
    for job in jobs:
        episode = episode_repo.get_by_id(job.episode_id)
        if episode:
            result.append(JobInfo(
                job_id=job.id,
                episode_id=job.episode_id,
                episode_title=episode.title,
                priority=job.priority,
            ))
    return result


@router.get("/status", response_model=QueueStatusResponse)
def get_queue_status():
    """Get queue and worker status."""
    manager = get_worker_manager()
    status = manager.get_status()

    with get_db() as conn:
        job_repo = JobRepository(conn)
        episode_repo = EpisodeRepository(conn)

        # Get running and queued jobs with episode names
        download_running = job_repo.get_running_jobs(JobType.DOWNLOAD)
        download_queued = job_repo.get_queued_jobs(JobType.DOWNLOAD, limit=20)
        transcribe_running = job_repo.get_running_jobs(JobType.TRANSCRIBE)
        transcribe_queued = job_repo.get_queued_jobs(JobType.TRANSCRIBE, limit=20)

        download_queue = QueueDetails(
            queued=status["download_queue"]["queued"],
            running=status["download_queue"]["running"],
            completed=status["download_queue"]["completed"],
            failed=status["download_queue"]["failed"],
            running_jobs=_get_job_infos(download_running, episode_repo),
            queued_jobs=_get_job_infos(download_queued, episode_repo),
        )

        transcribe_queue = QueueDetails(
            queued=status["transcribe_queue"]["queued"],
            running=status["transcribe_queue"]["running"],
            completed=status["transcribe_queue"]["completed"],
            failed=status["transcribe_queue"]["failed"],
            running_jobs=_get_job_infos(transcribe_running, episode_repo),
            queued_jobs=_get_job_infos(transcribe_queued, episode_repo),
        )

    return QueueStatusResponse(
        running=status["running"],
        download_workers=status["download_workers"],
        transcribe_workers=status["transcribe_workers"],
        download_queue=download_queue,
        transcribe_queue=transcribe_queue,
    )


@router.post("/episodes/{episode_id}/download", response_model=MessageResponse)
def queue_download(episode_id: int, request: QueueEpisodeRequest | None = None):
    """Queue an episode for download."""
    priority = request.priority if request else 10

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        job_repo = JobRepository(conn)

        episode = episode_repo.get_by_id(episode_id)
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")

        # Check if already queued
        if job_repo.has_pending_job(episode_id, JobType.DOWNLOAD):
            raise HTTPException(status_code=409, detail="Download already queued")

        # Check if already downloaded
        if episode.audio_path:
            raise HTTPException(status_code=409, detail="Episode already downloaded")

        job = job_repo.create(
            episode_id=episode_id,
            job_type=JobType.DOWNLOAD,
            priority=priority,
        )

    return MessageResponse(message="Download queued", job_id=job.id)


@router.post("/episodes/{episode_id}/transcribe", response_model=MessageResponse)
def queue_transcribe(episode_id: int, request: QueueEpisodeRequest | None = None):
    """Queue an episode for transcription."""
    priority = request.priority if request else 10

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        job_repo = JobRepository(conn)

        episode = episode_repo.get_by_id(episode_id)
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")

        # Check if downloaded
        if not episode.audio_path:
            raise HTTPException(status_code=400, detail="Episode not downloaded yet")

        # Check if already queued
        if job_repo.has_pending_job(episode_id, JobType.TRANSCRIBE):
            raise HTTPException(status_code=409, detail="Transcription already queued")

        job = job_repo.create(
            episode_id=episode_id,
            job_type=JobType.TRANSCRIBE,
            priority=priority,
        )

    return MessageResponse(message="Transcription queued", job_id=job.id)


@router.post("/episodes/{episode_id}/process", response_model=MessageResponse)
def queue_process(episode_id: int, request: QueueEpisodeRequest | None = None):
    """Queue an episode for download and transcription."""
    priority = request.priority if request else 10

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        job_repo = JobRepository(conn)

        episode = episode_repo.get_by_id(episode_id)
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")

        # Check if already has pending jobs
        if job_repo.has_pending_job(episode_id, JobType.DOWNLOAD):
            raise HTTPException(status_code=409, detail="Download already queued")

        if episode.audio_path:
            # Already downloaded, just queue transcription
            if job_repo.has_pending_job(episode_id, JobType.TRANSCRIBE):
                raise HTTPException(status_code=409, detail="Transcription already queued")

            job = job_repo.create(
                episode_id=episode_id,
                job_type=JobType.TRANSCRIBE,
                priority=priority,
            )
            return MessageResponse(message="Transcription queued (already downloaded)", job_id=job.id)

        # Queue download (transcription will be auto-queued after download)
        job = job_repo.create(
            episode_id=episode_id,
            job_type=JobType.DOWNLOAD,
            priority=priority,
        )

    return MessageResponse(message="Download queued (transcription will follow)", job_id=job.id)


@router.post("/{job_id}/retry", response_model=MessageResponse)
def retry_job(job_id: int):
    """Retry a failed job."""
    with get_db() as conn:
        repo = JobRepository(conn)

        job = repo.get_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.status != JobStatus.FAILED:
            raise HTTPException(status_code=400, detail="Can only retry failed jobs")

        # Reset job to queued
        conn.execute(
            """
            UPDATE job_queue
            SET status = 'queued', attempts = 0, error_message = NULL, next_retry_at = NULL
            WHERE id = ?
            """,
            (job_id,),
        )
        conn.commit()

    return MessageResponse(message="Job requeued", job_id=job_id)


@router.delete("/{job_id}", response_model=MessageResponse)
def cancel_job(job_id: int):
    """Cancel a queued job."""
    with get_db() as conn:
        repo = JobRepository(conn)

        job = repo.get_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.status == JobStatus.RUNNING:
            raise HTTPException(status_code=400, detail="Cannot cancel running job")

        deleted = repo.cancel_queued(job_id)
        if not deleted:
            raise HTTPException(status_code=400, detail="Job not in queued state")

    return MessageResponse(message="Job cancelled", job_id=job_id)


@router.post("/{job_id}/reset", response_model=MessageResponse)
def reset_job(job_id: int):
    """Force reset a running/stuck job back to queued state."""
    from cast2md.db.models import NodeStatus
    from cast2md.db.repository import TranscriberNodeRepository

    with get_db() as conn:
        repo = JobRepository(conn)

        job = repo.get_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.status != JobStatus.RUNNING:
            raise HTTPException(status_code=400, detail="Can only reset running jobs")

        reset = repo.force_reset(job_id)
        if not reset:
            raise HTTPException(status_code=400, detail="Failed to reset job")

        # Also clear any node that has this job assigned
        node_repo = TranscriberNodeRepository(conn)
        nodes = node_repo.get_all()
        for node in nodes:
            if node.current_job_id == job_id:
                node_repo.update_status(node.id, NodeStatus.ONLINE, current_job_id=None)

    return MessageResponse(message="Job reset to queued", job_id=job_id)


@router.delete("/{job_id}/force", response_model=MessageResponse)
def force_delete_job(job_id: int):
    """Delete a job regardless of status."""
    with get_db() as conn:
        repo = JobRepository(conn)

        job = repo.get_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        deleted = repo.delete(job_id)
        if not deleted:
            raise HTTPException(status_code=400, detail="Failed to delete job")

    return MessageResponse(message="Job deleted", job_id=job_id)


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: int):
    """Get job details."""
    with get_db() as conn:
        repo = JobRepository(conn)
        job = repo.get_by_id(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return _job_to_response(job)


# Batch operations

class BatchQueueRequest(BaseModel):
    """Request for batch queue operations."""

    priority: int = 10


class BatchQueueResponse(BaseModel):
    """Response for batch queue operations."""

    queued: int
    skipped: int
    message: str


@router.post("/batch/feed/{feed_id}/process", response_model=BatchQueueResponse)
def batch_queue_feed(feed_id: int, request: BatchQueueRequest | None = None):
    """Queue all pending episodes from a feed for processing."""
    from cast2md.db.models import EpisodeStatus

    priority = request.priority if request else 10

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        job_repo = JobRepository(conn)

        # Get all episodes for this feed and filter for pending
        # TODO: Add get_by_feed_and_status() for more efficient querying
        episodes = episode_repo.get_by_feed(feed_id, limit=10000)
        pending = [e for e in episodes if e.status == EpisodeStatus.PENDING]

        queued = 0
        skipped = 0

        for episode in pending:
            # Skip if already has pending job
            if job_repo.has_pending_job(episode.id, JobType.DOWNLOAD):
                skipped += 1
                continue

            job_repo.create(
                episode_id=episode.id,
                job_type=JobType.DOWNLOAD,
                priority=priority,
            )
            queued += 1

    return BatchQueueResponse(
        queued=queued,
        skipped=skipped,
        message=f"Queued {queued} episodes for processing ({skipped} skipped)",
    )


@router.post("/batch/all/process", response_model=BatchQueueResponse)
def batch_queue_all(request: BatchQueueRequest | None = None):
    """Queue all pending episodes across all feeds for processing."""
    from cast2md.db.models import EpisodeStatus
    from cast2md.db.repository import FeedRepository

    priority = request.priority if request else 10

    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)
        job_repo = JobRepository(conn)

        feeds = feed_repo.get_all()
        queued = 0
        skipped = 0

        for feed in feeds:
            # TODO: Add get_by_feed_and_status() for more efficient querying
            episodes = episode_repo.get_by_feed(feed.id, limit=10000)
            pending = [e for e in episodes if e.status == EpisodeStatus.PENDING]

            for episode in pending:
                if job_repo.has_pending_job(episode.id, JobType.DOWNLOAD):
                    skipped += 1
                    continue

                job_repo.create(
                    episode_id=episode.id,
                    job_type=JobType.DOWNLOAD,
                    priority=priority,
                )
                queued += 1

    return BatchQueueResponse(
        queued=queued,
        skipped=skipped,
        message=f"Queued {queued} episodes for processing ({skipped} skipped)",
    )


@router.delete("/batch/queued", response_model=BatchQueueResponse)
def batch_cancel_queued():
    """Cancel all queued jobs."""
    with get_db() as conn:
        job_repo = JobRepository(conn)

        # Get all queued jobs (high limit to ensure we get all pending jobs)
        queued_jobs = job_repo.get_queued_jobs(limit=10000)

        cancelled = 0
        for job in queued_jobs:
            if job_repo.cancel_queued(job.id):
                cancelled += 1

    return BatchQueueResponse(
        queued=0,
        skipped=0,
        message=f"Cancelled {cancelled} queued jobs",
    )


class BatchQueueByIdsRequest(BaseModel):
    """Request to queue specific episodes by ID."""

    episode_ids: list[int]
    priority: int = 10


@router.post("/batch/episodes", response_model=BatchQueueResponse)
def batch_queue_episodes(request: BatchQueueByIdsRequest):
    """Queue specific episodes by ID for processing."""
    from cast2md.db.models import EpisodeStatus

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        job_repo = JobRepository(conn)

        queued = 0
        skipped = 0

        for episode_id in request.episode_ids:
            episode = episode_repo.get_by_id(episode_id)
            if not episode:
                skipped += 1
                continue

            # Skip if not pending or already has a job
            if episode.status != EpisodeStatus.PENDING:
                skipped += 1
                continue

            if job_repo.has_pending_job(episode.id, JobType.DOWNLOAD):
                skipped += 1
                continue

            job_repo.create(
                episode_id=episode.id,
                job_type=JobType.DOWNLOAD,
                priority=request.priority,
            )
            queued += 1

    return BatchQueueResponse(
        queued=queued,
        skipped=skipped,
        message=f"Queued {queued} episodes for processing ({skipped} skipped)",
    )


class BatchQueueByRangeRequest(BaseModel):
    """Request to queue episodes by range (position or date)."""

    feed_id: int
    priority: int = 10
    # Position range (1-indexed, by published date descending)
    position_from: int | None = None
    position_to: int | None = None
    # Date range
    date_from: str | None = None  # ISO format YYYY-MM-DD
    date_to: str | None = None  # ISO format YYYY-MM-DD


@router.post("/batch/range", response_model=BatchQueueResponse)
def batch_queue_by_range(request: BatchQueueByRangeRequest):
    """Queue episodes by position range or date range."""
    from datetime import datetime

    from cast2md.db.models import EpisodeStatus

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        job_repo = JobRepository(conn)

        # Get all episodes for the feed (ordered by published_at DESC)
        # High limit needed to support position-based and date-based range filtering
        all_episodes = episode_repo.get_by_feed(request.feed_id, limit=10000)

        # Filter by position range if specified
        if request.position_from is not None or request.position_to is not None:
            start_idx = (request.position_from or 1) - 1  # Convert to 0-indexed
            end_idx = request.position_to or len(all_episodes)
            all_episodes = all_episodes[start_idx:end_idx]

        # Filter by date range if specified
        if request.date_from or request.date_to:
            date_from = datetime.fromisoformat(request.date_from) if request.date_from else None
            date_to = datetime.fromisoformat(request.date_to) if request.date_to else None

            filtered = []
            for ep in all_episodes:
                if not ep.published_at:
                    continue
                if date_from and ep.published_at < date_from:
                    continue
                if date_to and ep.published_at > date_to:
                    continue
                filtered.append(ep)
            all_episodes = filtered

        # Filter to pending only
        pending = [e for e in all_episodes if e.status == EpisodeStatus.PENDING]

        queued = 0
        skipped = 0

        for episode in pending:
            if job_repo.has_pending_job(episode.id, JobType.DOWNLOAD):
                skipped += 1
                continue

            job_repo.create(
                episode_id=episode.id,
                job_type=JobType.DOWNLOAD,
                priority=request.priority,
            )
            queued += 1

    return BatchQueueResponse(
        queued=queued,
        skipped=skipped,
        message=f"Queued {queued} episodes for processing ({skipped} skipped)",
    )


# Stuck job detection and management


class StuckJobInfo(BaseModel):
    """Stuck job with episode info."""

    job_id: int
    episode_id: int
    episode_title: str
    podcast_title: str
    job_type: str
    started_at: str
    runtime_seconds: int
    attempts: int
    max_attempts: int


class StuckJobsResponse(BaseModel):
    """Response for stuck jobs list."""

    stuck_count: int
    threshold_hours: int
    jobs: list[StuckJobInfo]


class AllJobInfo(BaseModel):
    """Job info with episode and podcast details."""

    job_id: int
    episode_id: int
    episode_title: str
    podcast_title: str
    job_type: str
    status: str
    is_stuck: bool
    priority: int
    attempts: int
    max_attempts: int
    created_at: str
    scheduled_at: str
    started_at: str | None
    completed_at: str | None
    runtime_seconds: int | None
    error_message: str | None


class AllJobsResponse(BaseModel):
    """Response for all jobs list."""

    total: int
    stuck_count: int
    jobs: list[AllJobInfo]


def _get_stuck_threshold() -> int:
    """Get stuck threshold hours from settings."""
    from cast2md.config.settings import get_settings
    return get_settings().stuck_threshold_hours


@router.get("/stuck", response_model=StuckJobsResponse)
def get_stuck_jobs(threshold_hours: int | None = None):
    """Get jobs that have been running longer than threshold."""
    from datetime import datetime, timedelta

    from cast2md.db.repository import FeedRepository

    if threshold_hours is None:
        threshold_hours = _get_stuck_threshold()

    with get_db() as conn:
        job_repo = JobRepository(conn)
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)

        stuck_jobs = job_repo.get_stuck_jobs(threshold_hours)

        jobs = []
        for job in stuck_jobs:
            episode = episode_repo.get_by_id(job.episode_id)
            if not episode:
                continue
            feed = feed_repo.get_by_id(episode.feed_id)

            runtime = 0
            if job.started_at:
                runtime = int((datetime.utcnow() - job.started_at).total_seconds())

            jobs.append(StuckJobInfo(
                job_id=job.id,
                episode_id=job.episode_id,
                episode_title=episode.title,
                podcast_title=feed.display_title if feed else "Unknown",
                job_type=job.job_type.value,
                started_at=job.started_at.isoformat() if job.started_at else "",
                runtime_seconds=runtime,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
            ))

    return StuckJobsResponse(
        stuck_count=len(jobs),
        threshold_hours=threshold_hours,
        jobs=jobs,
    )


@router.get("/all", response_model=AllJobsResponse)
def get_all_jobs(
    status: str | None = None,
    job_type: str | None = None,
    limit: int = 100,
):
    """Get all jobs with optional filtering."""
    from datetime import datetime, timedelta

    from cast2md.db.repository import FeedRepository

    # Validate status
    job_status = None
    if status:
        if status == "stuck":
            # Special filter for stuck jobs
            return _get_stuck_jobs_as_all_jobs(limit)
        try:
            job_status = JobStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    # Validate job_type
    jt = None
    if job_type:
        try:
            jt = JobType(job_type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid job type: {job_type}")

    with get_db() as conn:
        job_repo = JobRepository(conn)
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)

        threshold_hours = _get_stuck_threshold()
        jobs = job_repo.get_all_jobs(status=job_status, job_type=jt, limit=limit)
        stuck_threshold = datetime.utcnow() - timedelta(hours=threshold_hours)
        stuck_count = job_repo.count_stuck_jobs(threshold_hours)

        job_infos = []
        for job in jobs:
            episode = episode_repo.get_by_id(job.episode_id)
            if not episode:
                continue
            feed = feed_repo.get_by_id(episode.feed_id)

            # Calculate runtime for running jobs
            runtime = None
            is_stuck = False
            if job.status == JobStatus.RUNNING and job.started_at:
                runtime = int((datetime.utcnow() - job.started_at).total_seconds())
                is_stuck = job.started_at < stuck_threshold

            job_infos.append(AllJobInfo(
                job_id=job.id,
                episode_id=job.episode_id,
                episode_title=episode.title,
                podcast_title=feed.display_title if feed else "Unknown",
                job_type=job.job_type.value,
                status=job.status.value,
                is_stuck=is_stuck,
                priority=job.priority,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
                created_at=job.created_at.isoformat(),
                scheduled_at=job.scheduled_at.isoformat(),
                started_at=job.started_at.isoformat() if job.started_at else None,
                completed_at=job.completed_at.isoformat() if job.completed_at else None,
                runtime_seconds=runtime,
                error_message=job.error_message,
            ))

    return AllJobsResponse(
        total=len(job_infos),
        stuck_count=stuck_count,
        jobs=job_infos,
    )


def _get_stuck_jobs_as_all_jobs(limit: int) -> AllJobsResponse:
    """Get stuck jobs formatted as AllJobsResponse."""
    from datetime import datetime

    from cast2md.db.repository import FeedRepository

    threshold_hours = _get_stuck_threshold()

    with get_db() as conn:
        job_repo = JobRepository(conn)
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)

        stuck_jobs = job_repo.get_stuck_jobs(threshold_hours)[:limit]

        job_infos = []
        for job in stuck_jobs:
            episode = episode_repo.get_by_id(job.episode_id)
            if not episode:
                continue
            feed = feed_repo.get_by_id(episode.feed_id)

            runtime = None
            if job.started_at:
                runtime = int((datetime.utcnow() - job.started_at).total_seconds())

            job_infos.append(AllJobInfo(
                job_id=job.id,
                episode_id=job.episode_id,
                episode_title=episode.title,
                podcast_title=feed.display_title if feed else "Unknown",
                job_type=job.job_type.value,
                status=job.status.value,
                is_stuck=True,
                priority=job.priority,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
                created_at=job.created_at.isoformat(),
                scheduled_at=job.scheduled_at.isoformat(),
                started_at=job.started_at.isoformat() if job.started_at else None,
                completed_at=job.completed_at.isoformat() if job.completed_at else None,
                runtime_seconds=runtime,
                error_message=job.error_message,
            ))

    return AllJobsResponse(
        total=len(job_infos),
        stuck_count=len(job_infos),
        jobs=job_infos,
    )


@router.post("/batch/reset-stuck", response_model=BatchQueueResponse)
def batch_reset_stuck(threshold_hours: int | None = None):
    """Reset all stuck jobs back to queued state or fail them if max attempts exceeded."""
    if threshold_hours is None:
        threshold_hours = _get_stuck_threshold()

    with get_db() as conn:
        job_repo = JobRepository(conn)
        requeued, failed = job_repo.batch_force_reset_stuck(threshold_hours)

    return BatchQueueResponse(
        queued=requeued,
        skipped=failed,
        message=f"Reset {requeued} stuck jobs to queued, {failed} failed (max attempts)",
    )


@router.post("/batch/retry-failed", response_model=BatchQueueResponse)
def batch_retry_failed():
    """Retry all failed jobs."""
    with get_db() as conn:
        job_repo = JobRepository(conn)
        count = job_repo.batch_retry_failed()

    return BatchQueueResponse(
        queued=count,
        skipped=0,
        message=f"Retrying {count} failed jobs",
    )


class CleanupRequest(BaseModel):
    """Request for cleanup operations."""

    older_than_days: int = 7


@router.delete("/batch/completed", response_model=BatchQueueResponse)
def batch_delete_completed(older_than_days: int = 7):
    """Delete completed/failed jobs older than N days."""
    with get_db() as conn:
        job_repo = JobRepository(conn)
        count = job_repo.cleanup_completed(older_than_days)

    return BatchQueueResponse(
        queued=0,
        skipped=0,
        message=f"Deleted {count} old completed/failed jobs",
    )
