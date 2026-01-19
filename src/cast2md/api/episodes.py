"""Episode API endpoints."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cast2md.db.connection import get_db
from cast2md.db.models import Episode, EpisodeStatus
from cast2md.db.repository import EpisodeRepository, FeedRepository
from cast2md.download.downloader import download_episode
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

        # Get all episodes (we'll implement pagination later)
        episodes = episode_repo.get_by_feed(feed_id, limit=limit + offset)

        # Apply offset
        episodes = episodes[offset : offset + limit]

        # Get total count
        all_episodes = episode_repo.get_by_feed(feed_id, limit=10000)
        total = len(all_episodes)

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
    """Trigger download for an episode."""
    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)

        episode = episode_repo.get_by_id(episode_id)
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")

        feed = feed_repo.get_by_id(episode.feed_id)
        if not feed:
            raise HTTPException(status_code=404, detail="Feed not found")

    # Check if already downloaded
    if episode.audio_path and Path(episode.audio_path).exists():
        return MessageResponse(
            message="Episode already downloaded",
            path=episode.audio_path,
        )

    try:
        audio_path = download_episode(episode, feed)
        return MessageResponse(
            message="Download completed",
            path=str(audio_path),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download failed: {e}")


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
