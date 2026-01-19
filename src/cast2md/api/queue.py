"""Queue management API endpoints."""

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


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: int):
    """Get job details."""
    with get_db() as conn:
        repo = JobRepository(conn)
        job = repo.get_by_id(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return _job_to_response(job)
