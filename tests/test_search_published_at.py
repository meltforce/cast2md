"""The type of published_at across the repository and the API boundary.

The repository dataclasses declared `published_at: str | None` while assigning
the timestamp column straight from the row, where psycopg2 hands back a
`datetime`. Nothing enforced the annotation, so the lie survived until pydantic
validated it and `GET /api/search/transcripts` answered 500 for every query
that produced a non-empty tsquery.

The decision taken on 2026-08-07: the repository carries `datetime`, matching
`Episode.published_at`, and the API serialises at the boundary. These tests
pin both halves, because three separate workarounds had grown around the
ambiguity before it was fixed.
"""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from cast2md.search.repository import TranscriptSearchRepository

TRANSCRIPT = """# Episode Transcript

*Language: en (100.0% confidence)*

**[00:00]** Hello and welcome to the podcast about distributed systems.

**[00:30]** Today we discuss consensus algorithms and their trade-offs.
"""


@pytest.fixture
def indexed_episode(db_conn, feed_repo, episode_repo):
    """A feed and one indexed episode with a known published_at."""
    feed = feed_repo.create(
        url="https://example.invalid/published-at.xml",
        title="Published At Feed",
        description="Fixture feed",
    )
    published = datetime(2025, 10, 7, 2, 0)
    episode = episode_repo.create(
        feed_id=feed.id,
        guid="published-at-1",
        title="Consensus Algorithms",
        audio_url="https://example.invalid/1.mp3",
        published_at=published,
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(TRANSCRIPT)
        path = Path(f.name)

    TranscriptSearchRepository(db_conn).index_episode(episode.id, str(path))
    yield episode, published
    path.unlink(missing_ok=True)


def test_search_returns_published_at_as_datetime(db_conn, indexed_episode):
    """The repository hands back the column, not a formatted string."""
    _, published = indexed_episode

    response = TranscriptSearchRepository(db_conn).search(query="consensus", limit=5)

    assert response.results, "the fixture transcript should match 'consensus'"
    for result in response.results:
        assert isinstance(result.published_at, datetime)
        assert result.published_at == published


def test_search_episode_returns_published_at_as_datetime(db_conn, indexed_episode):
    """Same for the per-episode search, which feeds the same response model."""
    episode, published = indexed_episode

    results = TranscriptSearchRepository(db_conn).search_episode(
        episode_id=episode.id, query="consensus", limit=5
    )

    assert results
    assert all(r.published_at == published for r in results)
    assert all(isinstance(r.published_at, datetime) for r in results)


def test_transcript_search_endpoint_serialises_published_at(db_conn, indexed_episode):
    """The API answers 200 and the field is an ISO string, not a datetime.

    The regression this pins returned 500 with a pydantic `string_type` error.
    """
    import importlib

    from fastapi.testclient import TestClient

    main = importlib.import_module("cast2md.main")
    _, published = indexed_episode

    response = TestClient(main.app).get("/api/search/transcripts", params={"q": "consensus"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["results"], "the fixture transcript should match 'consensus'"
    for result in body["results"]:
        assert isinstance(result["published_at"], str)
        assert result["published_at"] == published.isoformat()
