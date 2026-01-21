"""Pytest fixtures for cast2md tests."""

import sqlite3
from datetime import datetime

import pytest

from cast2md.db.migrations import run_migrations
from cast2md.db.models import JobType
from cast2md.db.repository import (
    EpisodeRepository,
    FeedRepository,
    JobRepository,
)
from cast2md.search.repository import TranscriptSearchRepository
from cast2md.db.schema import get_schema


@pytest.fixture
def db_conn():
    """Create an in-memory SQLite database with schema."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")

    # Create schema
    conn.executescript(get_schema())

    # Run migrations to add additional columns
    run_migrations(conn)

    yield conn

    conn.close()


@pytest.fixture
def feed_repo(db_conn):
    """Create a FeedRepository instance."""
    return FeedRepository(db_conn)


@pytest.fixture
def episode_repo(db_conn):
    """Create an EpisodeRepository instance."""
    return EpisodeRepository(db_conn)


@pytest.fixture
def job_repo(db_conn):
    """Create a JobRepository instance."""
    return JobRepository(db_conn)


@pytest.fixture
def search_repo(db_conn):
    """Create a TranscriptSearchRepository instance."""
    return TranscriptSearchRepository(db_conn)


@pytest.fixture
def sample_feed(feed_repo):
    """Create a sample feed for testing."""
    return feed_repo.create(
        url="https://example.com/feed.xml",
        title="Test Podcast",
        description="A test podcast feed",
    )


@pytest.fixture
def sample_episode(episode_repo, sample_feed):
    """Create a sample episode for testing."""
    return episode_repo.create(
        feed_id=sample_feed.id,
        guid="test-episode-1",
        title="Test Episode 1",
        audio_url="https://example.com/episode1.mp3",
        description="Test episode description",
        published_at=datetime.utcnow(),
    )


@pytest.fixture
def sample_job(job_repo, sample_episode):
    """Create a sample job for testing."""
    return job_repo.create(
        episode_id=sample_episode.id,
        job_type=JobType.DOWNLOAD,
        priority=10,
        max_attempts=3,
    )
