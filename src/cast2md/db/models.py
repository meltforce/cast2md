"""Data models for the database layer."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class EpisodeStatus(str, Enum):
    """Episode processing status."""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    TRANSCRIBING = "transcribing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobType(str, Enum):
    """Job type for queue."""

    DOWNLOAD = "download"
    TRANSCRIBE = "transcribe"


class JobStatus(str, Enum):
    """Job status in queue."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Feed:
    """Podcast feed model."""

    id: Optional[int]
    url: str
    title: str
    description: Optional[str]
    image_url: Optional[str]
    last_polled: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: tuple) -> "Feed":
        """Create Feed from database row."""
        return cls(
            id=row[0],
            url=row[1],
            title=row[2],
            description=row[3],
            image_url=row[4],
            last_polled=datetime.fromisoformat(row[5]) if row[5] else None,
            created_at=datetime.fromisoformat(row[6]),
            updated_at=datetime.fromisoformat(row[7]),
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
    transcript_url: Optional[str]  # Podcast 2.0 transcript URL
    error_message: Optional[str]
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
            published_at=datetime.fromisoformat(row[7]) if row[7] else None,
            status=EpisodeStatus(row[8]),
            audio_path=row[9],
            transcript_path=row[10],
            transcript_url=row[11],
            error_message=row[12],
            created_at=datetime.fromisoformat(row[13]),
            updated_at=datetime.fromisoformat(row[14]),
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
            scheduled_at=datetime.fromisoformat(row[7]) if row[7] else datetime.utcnow(),
            started_at=datetime.fromisoformat(row[8]) if row[8] else None,
            completed_at=datetime.fromisoformat(row[9]) if row[9] else None,
            next_retry_at=datetime.fromisoformat(row[10]) if row[10] else None,
            error_message=row[11],
            created_at=datetime.fromisoformat(row[12]),
        )
