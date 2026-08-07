"""Data models for the database layer."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum


def parse_datetime(value) -> datetime | None:
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

    id: int | None
    url: str
    title: str
    description: str | None
    image_url: str | None
    author: str | None
    link: str | None
    categories: str | None  # JSON string
    custom_title: str | None
    last_polled: datetime | None
    itunes_id: str | None
    pocketcasts_uuid: str | None
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

    id: int | None
    feed_id: int
    guid: str
    title: str
    description: str | None
    audio_url: str
    duration_seconds: int | None
    published_at: datetime | None
    status: EpisodeStatus
    audio_path: str | None
    transcript_path: str | None
    transcript_url: str | None  # Podcast 2.0 transcript URL from RSS
    transcript_model: str | None  # Whisper model used for transcription
    transcript_source: str | None  # e.g., 'whisper', 'podcast2.0:vtt', 'pocketcasts'
    transcript_type: str | None  # MIME type of original transcript
    pocketcasts_transcript_url: str | None  # Pocket Casts transcript URL (discovered upfront)
    transcript_checked_at: datetime | None  # When transcript was last checked
    next_transcript_retry_at: datetime | None  # When to retry transcript download
    transcript_failure_reason: str | None  # Error type (e.g., 'forbidden', 'not_found')
    link: str | None
    author: str | None
    error_message: str | None
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

    id: int | None
    episode_id: int
    job_type: JobType
    priority: int
    status: JobStatus
    attempts: int
    max_attempts: int
    scheduled_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    next_retry_at: datetime | None
    error_message: str | None
    created_at: datetime
    assigned_node_id: str | None = None
    claimed_at: datetime | None = None
    progress_percent: int | None = None

    def is_stuck(self, threshold_minutes: int) -> bool:
        """Whether this job has been running longer than the threshold.

        Args:
            threshold_minutes: Minutes after which a running job is considered stuck.

        Returns:
            True only for running jobs that started before the threshold.
        """
        if self.status != JobStatus.RUNNING or not self.started_at:
            return False
        return self.started_at < datetime.now() - timedelta(minutes=threshold_minutes)

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
    whisper_model: str | None
    whisper_backend: str | None
    status: NodeStatus
    last_heartbeat: datetime | None
    current_job_id: int | None
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
