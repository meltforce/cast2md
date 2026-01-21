"""Episode API endpoints."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from cast2md.db.connection import get_db
from cast2md.db.models import Episode, EpisodeStatus
from cast2md.db.repository import EpisodeRepository, FeedRepository
from cast2md.download.downloader import download_episode
from cast2md.export.formats import export_transcript
from cast2md.transcription.service import transcribe_episode

router = APIRouter(prefix="/api", tags=["episodes"])


class EpisodeResponse(BaseModel):
    """Response model for an episode."""

    id: int
    feed_id: int
    guid: str
    title: str
    description: str | None
    audio_url: str
    duration_seconds: int | None
    published_at: str | None
    status: str
    audio_path: str | None
    transcript_path: str | None
    transcript_url: str | None
    transcript_source: str | None
    transcript_model: str | None
    error_message: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_episode(cls, episode: Episode) -> "EpisodeResponse":
        return cls(
            id=episode.id,
            feed_id=episode.feed_id,
            guid=episode.guid,
            title=episode.title,
            description=episode.description,
            audio_url=episode.audio_url,
            duration_seconds=episode.duration_seconds,
            published_at=episode.published_at.isoformat() if episode.published_at else None,
            status=episode.status.value,
            audio_path=episode.audio_path,
            transcript_path=episode.transcript_path,
            transcript_url=episode.transcript_url,
            transcript_source=episode.transcript_source,
            transcript_model=episode.transcript_model,
            error_message=episode.error_message,
            created_at=episode.created_at.isoformat(),
            updated_at=episode.updated_at.isoformat(),
        )


class EpisodeListResponse(BaseModel):
    """Response model for episode list."""

    episodes: list[EpisodeResponse]
    total: int


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str
    path: str | None = None


@router.get("/feeds/{feed_id}/episodes", response_model=EpisodeListResponse)
def list_episodes(feed_id: int, limit: int = 50, offset: int = 0):
    """List episodes for a feed."""
    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)

        feed = feed_repo.get_by_id(feed_id)
        if not feed:
            raise HTTPException(status_code=404, detail="Feed not found")

        # Get episodes with pagination
        episodes = episode_repo.get_by_feed(feed_id, limit=limit + offset)
        episodes = episodes[offset : offset + limit]

        # Get total count using COUNT(*)
        total = episode_repo.count_by_feed(feed_id)

    return EpisodeListResponse(
        episodes=[EpisodeResponse.from_episode(ep) for ep in episodes],
        total=total,
    )


@router.get("/episodes/{episode_id}", response_model=EpisodeResponse)
def get_episode(episode_id: int):
    """Get an episode by ID."""
    with get_db() as conn:
        repo = EpisodeRepository(conn)
        episode = repo.get_by_id(episode_id)

    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    return EpisodeResponse.from_episode(episode)


@router.post("/episodes/{episode_id}/download", response_model=MessageResponse)
def trigger_download(episode_id: int):
    """Trigger download and transcription for an episode."""
    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)

        episode = episode_repo.get_by_id(episode_id)
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")

        feed = feed_repo.get_by_id(episode.feed_id)
        if not feed:
            raise HTTPException(status_code=404, detail="Feed not found")

    # Check if already processed or in progress
    if episode.status in (EpisodeStatus.DOWNLOADING, EpisodeStatus.TRANSCRIBING):
        return MessageResponse(
            message=f"Already in progress: {episode.status.value}",
            path=None,
        )

    if episode.status == EpisodeStatus.COMPLETED:
        return MessageResponse(
            message="Already completed",
            path=episode.transcript_path,
        )

    if episode.audio_path and Path(episode.audio_path).exists():
        # Downloaded but not transcribed - just transcribe
        transcript_path = transcribe_episode(episode, feed)
        return MessageResponse(
            message="Transcription completed",
            path=str(transcript_path),
        )

    try:
        audio_path = download_episode(episode, feed)

        # Re-fetch episode to get updated status
        with get_db() as conn:
            episode_repo = EpisodeRepository(conn)
            episode = episode_repo.get_by_id(episode_id)

        # Only transcribe if not already done or in progress
        if episode.status == EpisodeStatus.DOWNLOADED:
            transcript_path = transcribe_episode(episode, feed)
            return MessageResponse(
                message="Download and transcription completed",
                path=str(transcript_path),
            )
        elif episode.status == EpisodeStatus.COMPLETED:
            return MessageResponse(
                message="Already transcribed",
                path=episode.transcript_path,
            )
        else:
            return MessageResponse(
                message=f"Download completed, status: {episode.status.value}",
                path=str(audio_path),
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing failed: {e}")


@router.post("/episodes/{episode_id}/transcribe", response_model=MessageResponse)
def trigger_transcribe(episode_id: int, timestamps: bool = False):
    """Trigger transcription for an episode."""
    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)

        episode = episode_repo.get_by_id(episode_id)
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")

        feed = feed_repo.get_by_id(episode.feed_id)
        if not feed:
            raise HTTPException(status_code=404, detail="Feed not found")

    # Check if already completed or in progress
    if episode.status == EpisodeStatus.COMPLETED:
        return MessageResponse(
            message="Already transcribed",
            path=episode.transcript_path,
        )

    if episode.status == EpisodeStatus.TRANSCRIBING:
        return MessageResponse(
            message="Transcription already in progress",
            path=None,
        )

    # Check if downloaded
    if not episode.audio_path:
        raise HTTPException(
            status_code=400,
            detail="Episode not downloaded. Download first.",
        )

    if not Path(episode.audio_path).exists():
        raise HTTPException(
            status_code=400,
            detail="Audio file not found. Re-download required.",
        )

    try:
        transcript_path = transcribe_episode(episode, feed, include_timestamps=timestamps)
        return MessageResponse(
            message="Transcription completed",
            path=str(transcript_path),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")


@router.get("/episodes/status/{status}", response_model=EpisodeListResponse)
def list_episodes_by_status(status: str, limit: int = 100):
    """List episodes by status."""
    try:
        episode_status = EpisodeStatus(status)
    except ValueError:
        valid_statuses = [s.value for s in EpisodeStatus]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Valid options: {valid_statuses}",
        )

    with get_db() as conn:
        repo = EpisodeRepository(conn)
        episodes = repo.get_by_status(episode_status, limit=limit)

    return EpisodeListResponse(
        episodes=[EpisodeResponse.from_episode(ep) for ep in episodes],
        total=len(episodes),
    )


@router.get("/episodes/{episode_id}/transcript")
def get_transcript(episode_id: int, format: str = "md"):
    """Download transcript in specified format.

    Supported formats:
    - md: Markdown (original format)
    - txt: Plain text (no timestamps)
    - srt: SRT subtitles
    - vtt: WebVTT subtitles
    - json: JSON with segments
    """
    valid_formats = ["md", "txt", "srt", "vtt", "json"]
    if format not in valid_formats:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid format. Valid options: {valid_formats}",
        )

    with get_db() as conn:
        repo = EpisodeRepository(conn)
        episode = repo.get_by_id(episode_id)

    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    if not episode.transcript_path:
        raise HTTPException(status_code=404, detail="Transcript not available")

    transcript_path = Path(episode.transcript_path)
    if not transcript_path.exists():
        raise HTTPException(status_code=404, detail="Transcript file not found")

    try:
        content, filename, content_type = export_transcript(transcript_path, format)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")

    return PlainTextResponse(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
