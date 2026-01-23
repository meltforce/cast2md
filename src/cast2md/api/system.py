"""System API endpoints."""

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cast2md.config.settings import get_settings
from cast2md.db.connection import get_db
from cast2md.db.models import EpisodeStatus
from cast2md.db.repository import EpisodeRepository, FeedRepository

router = APIRouter(prefix="/api", tags=["system"])


class StatusCounts(BaseModel):
    """Episode counts by status."""

    new: int = 0
    awaiting_transcript: int = 0
    needs_audio: int = 0
    downloading: int = 0
    audio_ready: int = 0
    transcribing: int = 0
    completed: int = 0
    failed: int = 0
    total: int = 0


class SystemStatus(BaseModel):
    """System status response."""

    feed_count: int
    episode_counts: StatusCounts
    whisper_model: str
    whisper_device: str
    storage_path: str
    database_url: str


@router.get("/status", response_model=SystemStatus)
def get_status():
    """Get system status."""
    settings = get_settings()

    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)

        feeds = feed_repo.get_all()
        status_counts = episode_repo.count_by_status()

    counts = StatusCounts(
        new=status_counts.get(EpisodeStatus.NEW.value, 0),
        awaiting_transcript=status_counts.get(EpisodeStatus.AWAITING_TRANSCRIPT.value, 0),
        needs_audio=status_counts.get(EpisodeStatus.NEEDS_AUDIO.value, 0),
        downloading=status_counts.get(EpisodeStatus.DOWNLOADING.value, 0),
        audio_ready=status_counts.get(EpisodeStatus.AUDIO_READY.value, 0),
        transcribing=status_counts.get(EpisodeStatus.TRANSCRIBING.value, 0),
        completed=status_counts.get(EpisodeStatus.COMPLETED.value, 0),
        failed=status_counts.get(EpisodeStatus.FAILED.value, 0),
    )
    counts.total = sum([
        counts.new,
        counts.awaiting_transcript,
        counts.needs_audio,
        counts.downloading,
        counts.audio_ready,
        counts.transcribing,
        counts.completed,
        counts.failed,
    ])

    return SystemStatus(
        feed_count=len(feeds),
        episode_counts=counts,
        whisper_model=settings.whisper_model,
        whisper_device=settings.whisper_device,
        storage_path=str(settings.storage_path),
        database_url=settings.database_url.split("@")[-1] if "@" in settings.database_url else settings.database_url,
    )


class HealthCheck(BaseModel):
    """Health check response."""

    status: Literal["healthy", "unhealthy"]
    database: bool
    storage: bool
    message: str | None = None


@router.get("/health", response_model=HealthCheck)
def health_check():
    """Health check endpoint for load balancers and monitoring.

    Returns 200 if healthy, 503 if unhealthy.
    """
    settings = get_settings()
    checks = {"database": False, "storage": False}
    errors = []

    # Check database connectivity
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
        checks["database"] = True
    except Exception as e:
        errors.append(f"Database: {e}")

    # Check storage directory
    try:
        if settings.storage_path.exists() and settings.storage_path.is_dir():
            checks["storage"] = True
        else:
            errors.append("Storage path does not exist")
    except Exception as e:
        errors.append(f"Storage: {e}")

    # Determine overall health
    is_healthy = all(checks.values())
    status = "healthy" if is_healthy else "unhealthy"
    message = "; ".join(errors) if errors else None

    response = HealthCheck(
        status=status,
        database=checks["database"],
        storage=checks["storage"],
        message=message,
    )

    if not is_healthy:
        return JSONResponse(content=response.model_dump(), status_code=503)

    return response
