"""Data models for the database layer."""

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


def parse_datetime(value) -> Optional[datetime]:
    """Parse a datetime value from database.

    Handles both ISO format strings and native datetime objects.

    Args:
        value: A datetime object, ISO format string, or None.

    Returns:
        Parsed datetime or None.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return None


class EpisodeStatus(str, Enum):
    """Episode processing status."""

    NEW = "new"  # Just discovered, ready to process
    AWAITING_TRANSCRIPT = "awaiting_transcript"  # Checking external sources, will retry
    NEEDS_AUDIO = "needs_audio"  # No external transcript, audio download required
    DOWNLOADING = "downloading"
    AUDIO_READY = "audio_ready"  # Audio downloaded, ready for Whisper
    TRANSCRIBING = "transcribing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobType(str, Enum):
    """Job type for queue."""

    DOWNLOAD = "download"
    TRANSCRIBE = "transcribe"
    TRANSCRIPT_DOWNLOAD = "transcript_download"
    EMBED = "embed"


class JobStatus(str, Enum):
    """Job status in queue."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class NodeStatus(str, Enum):
    """Transcriber node status."""

    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"


@dataclass
class Feed:
    """Podcast feed model."""

    id: Optional[int]
    url: str
    title: str
    description: Optional[str]
    image_url: Optional[str]
    author: Optional[str]
    link: Optional[str]
    categories: Optional[str]  # JSON string
    custom_title: Optional[str]
    last_polled: Optional[datetime]
    itunes_id: Optional[str]
    pocketcasts_uuid: Optional[str]
    created_at: datetime
    updated_at: datetime

    @property
    def display_title(self) -> str:
        """Return custom_title if set, otherwise the RSS title."""
        return self.custom_title or self.title

    @property
    def category_list(self) -> list[str]:
        """Parse categories JSON to list."""
        if not self.categories:
            return []
        try:
            return json.loads(self.categories)
        except (json.JSONDecodeError, TypeError):
            return []

    @classmethod
    def from_row(cls, row: tuple) -> "Feed":
        """Create Feed from database row."""
        return cls(
            id=row[0],
            url=row[1],
            title=row[2],
            description=row[3],
            image_url=row[4],
            author=row[5],
            link=row[6],
            categories=row[7],
            custom_title=row[8],
            last_polled=parse_datetime(row[9]),
            itunes_id=row[10] if len(row) > 10 else None,
            pocketcasts_uuid=row[11] if len(row) > 11 else None,
            created_at=parse_datetime(row[12]) or datetime.now(),
            updated_at=parse_datetime(row[13]) or datetime.now(),
        )


@dataclass
class Episode:
    """Podcast episode model."""

    id: Optional[int]
    feed_id: int
    guid: str
    title: str
    description: Optional[str]
    audio_url: str
    duration_seconds: Optional[int]
    published_at: Optional[datetime]
    status: EpisodeStatus
    audio_path: Optional[str]
    transcript_path: Optional[str]
    transcript_url: Optional[str]  # Podcast 2.0 transcript URL from RSS
    transcript_model: Optional[str]  # Whisper model used for transcription
    transcript_source: Optional[str]  # e.g., 'whisper', 'podcast2.0:vtt', 'pocketcasts'
    transcript_type: Optional[str]  # MIME type of original transcript
    pocketcasts_transcript_url: Optional[str]  # Pocket Casts transcript URL (discovered upfront)
    transcript_checked_at: Optional[datetime]  # When transcript was last checked
    next_transcript_retry_at: Optional[datetime]  # When to retry transcript download
    transcript_failure_reason: Optional[str]  # Error type (e.g., 'forbidden', 'not_found')
    link: Optional[str]
    author: Optional[str]
    error_message: Optional[str]
    permanent_failure: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: tuple) -> "Episode":
        """Create Episode from database row."""
        return cls(
            id=row[0],
            feed_id=row[1],
            guid=row[2],
            title=row[3],
            description=row[4],
            audio_url=row[5],
            duration_seconds=row[6],
            published_at=parse_datetime(row[7]),
            status=EpisodeStatus(row[8]),
            audio_path=row[9],
            transcript_path=row[10],
            transcript_url=row[11],
            transcript_model=row[12],
            transcript_source=row[13] if len(row) > 13 else None,
            transcript_type=row[14] if len(row) > 14 else None,
            pocketcasts_transcript_url=row[15] if len(row) > 15 else None,
            transcript_checked_at=parse_datetime(row[16]) if len(row) > 16 else None,
            next_transcript_retry_at=parse_datetime(row[17]) if len(row) > 17 else None,
            transcript_failure_reason=row[18] if len(row) > 18 else None,
            link=row[19] if len(row) > 19 else None,
            author=row[20] if len(row) > 20 else None,
            error_message=row[21] if len(row) > 21 else None,
            permanent_failure=bool(row[22]) if len(row) > 22 else False,
            created_at=parse_datetime(row[23]) or datetime.now(),
            updated_at=parse_datetime(row[24]) or datetime.now(),
        )


@dataclass
class Job:
    """Job queue entry."""

    id: Optional[int]
    episode_id: int
    job_type: JobType
    priority: int
    status: JobStatus
    attempts: int
    max_attempts: int
    scheduled_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    next_retry_at: Optional[datetime]
    error_message: Optional[str]
    created_at: datetime
    assigned_node_id: Optional[str] = None
    claimed_at: Optional[datetime] = None
    progress_percent: Optional[int] = None

    @classmethod
    def from_row(cls, row: tuple) -> "Job":
        """Create Job from database row."""
        return cls(
            id=row[0],
            episode_id=row[1],
            job_type=JobType(row[2]),
            priority=row[3],
            status=JobStatus(row[4]),
            attempts=row[5],
            max_attempts=row[6],
            scheduled_at=parse_datetime(row[7]) or datetime.now(),
            started_at=parse_datetime(row[8]),
            completed_at=parse_datetime(row[9]),
            next_retry_at=parse_datetime(row[10]),
            error_message=row[11],
            created_at=parse_datetime(row[12]) or datetime.now(),
            assigned_node_id=row[13] if len(row) > 13 else None,
            claimed_at=parse_datetime(row[14]) if len(row) > 14 else None,
            progress_percent=row[15] if len(row) > 15 else None,
        )


@dataclass
class TranscriberNode:
    """Remote transcriber node."""

    id: str
    name: str
    url: str
    api_key: str
    whisper_model: Optional[str]
    whisper_backend: Optional[str]
    status: NodeStatus
    last_heartbeat: Optional[datetime]
    current_job_id: Optional[int]
    priority: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: tuple) -> "TranscriberNode":
        """Create TranscriberNode from database row."""
        return cls(
            id=row[0],
            name=row[1],
            url=row[2],
            api_key=row[3],
            whisper_model=row[4],
            whisper_backend=row[5],
            status=NodeStatus(row[6]) if row[6] else NodeStatus.OFFLINE,
            last_heartbeat=parse_datetime(row[7]),
            current_job_id=row[8],
            priority=row[9] if row[9] is not None else 10,
            created_at=parse_datetime(row[10]) or datetime.now(),
            updated_at=parse_datetime(row[11]) or datetime.now(),
        )
