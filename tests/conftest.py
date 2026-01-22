"""Pytest fixtures for cast2md tests."""

from datetime import datetime

import pytest

from cast2md.db.connection import get_connection, _return_pg_connection, init_db
from cast2md.db.models import JobType
from cast2md.db.repository import (
    EpisodeRepository,
    FeedRepository,
    JobRepository,
)
from cast2md.search.repository import TranscriptSearchRepository


@pytest.fixture
def db_conn():
    """Get a PostgreSQL connection with clean test data.

    Cleans up test data before each test to ensure isolation.
    Uses DELETE with cascading to clean dependent tables.
    """
    # Ensure schema exists (idempotent)
    init_db()

    conn = get_connection()

    # Clean up test data before each test
    # Delete in order that respects foreign keys (or rely on CASCADE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM job_queue")
    cursor.execute("DELETE FROM transcript_segments")
    cursor.execute("DELETE FROM segment_embeddings")
    cursor.execute("DELETE FROM episode_search")
    cursor.execute("DELETE FROM episode")
    cursor.execute("DELETE FROM feed")
    conn.commit()

    yield conn

    _return_pg_connection(conn)


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
