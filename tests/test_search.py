"""Integration tests for TranscriptSearchRepository."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from cast2md.search.repository import TranscriptSearchRepository


@pytest.fixture
def search_repo(db_conn):
    """Create a TranscriptSearchRepository instance."""
    return TranscriptSearchRepository(db_conn)


@pytest.fixture
def transcript_file():
    """Create a temporary transcript file with known content.

    Uses the expected format: **[MM:SS]** followed by text.
    """
    content = """# Episode Transcript

*Language: en (100.0% confidence)*

**[00:00]** Hello and welcome to the podcast. Today we're discussing artificial intelligence and machine learning in the modern world.

**[00:30]** Our guest today is an expert in deep learning and neural networks. She has published many papers on the topic.

**[01:00]** Let's talk about the future of AI. Will robots replace human workers? This is a question many people are asking.

**[01:30]** The answer is complex. Some jobs will be automated, but new jobs will emerge. Technology creates opportunities as well as challenges.
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(content)
        f.flush()
        yield Path(f.name)
    # Cleanup
    Path(f.name).unlink(missing_ok=True)


@pytest.fixture
def indexed_episode(db_conn, sample_episode, search_repo, transcript_file):
    """Create an episode with an indexed transcript."""
    # Update episode with transcript path
    db_conn.execute(
        "UPDATE episode SET transcript_path = ?, status = 'completed' WHERE id = ?",
        (str(transcript_file), sample_episode.id),
    )
    db_conn.commit()

    # Index the transcript
    segment_count = search_repo.index_episode(sample_episode.id, str(transcript_file))

    return sample_episode, segment_count


class TestIndexEpisode:
    """Tests for index_episode method."""

    def test_index_episode_creates_segments(self, search_repo, sample_episode, transcript_file):
        """Test that indexing creates the expected number of segments."""
        count = search_repo.index_episode(sample_episode.id, str(transcript_file))

        # Should have 4 segments (one per ## section)
        assert count == 4

    def test_index_episode_nonexistent_file(self, search_repo, sample_episode):
        """Test indexing a nonexistent file returns 0."""
        count = search_repo.index_episode(sample_episode.id, "/nonexistent/path.md")
        assert count == 0

    def test_index_episode_replaces_existing(self, search_repo, sample_episode, transcript_file):
        """Test that re-indexing replaces existing segments."""
        # Index twice
        search_repo.index_episode(sample_episode.id, str(transcript_file))
        count = search_repo.index_episode(sample_episode.id, str(transcript_file))

        # Should still have 4 segments (not 8)
        assert count == 4

        # Verify only 4 segments in DB
        indexed = search_repo.get_indexed_count()
        assert indexed == 4


class TestSearchBasic:
    """Tests for basic search functionality."""

    def test_search_basic_returns_results(self, search_repo, indexed_episode):
        """Test basic search returns matching results."""
        episode, _ = indexed_episode

        response = search_repo.search("artificial intelligence")

        assert response.total > 0
        assert len(response.results) > 0
        assert response.results[0].episode_id == episode.id

    def test_search_no_results(self, search_repo, indexed_episode):
        """Test search with no matches returns empty response."""
        response = search_repo.search("xyznonexistent123")

        assert response.total == 0
        assert len(response.results) == 0

    def test_search_empty_query(self, search_repo, indexed_episode):
        """Test empty query returns empty response."""
        response = search_repo.search("")

        assert response.total == 0
        assert len(response.results) == 0

    def test_search_result_has_snippet(self, search_repo, indexed_episode):
        """Test search results include snippets with highlights."""
        response = search_repo.search("machine learning")

        assert len(response.results) > 0
        assert response.results[0].snippet is not None
        # FTS5 highlights matches with <mark> tags
        assert "learning" in response.results[0].snippet.lower()


class TestSearchPhrase:
    """Tests for phrase search."""

    def test_search_phrase_exact_match(self, search_repo, indexed_episode):
        """Test quoted phrase search works."""
        response = search_repo.search('"deep learning"')

        assert response.total > 0
        assert any("deep" in r.snippet.lower() and "learning" in r.snippet.lower()
                   for r in response.results)

    def test_search_phrase_no_match(self, search_repo, indexed_episode):
        """Test phrase that doesn't exist returns no results."""
        # This phrase doesn't exist as a unit
        response = search_repo.search('"learning artificial"')

        assert response.total == 0


class TestSearchBoolean:
    """Tests for boolean operators (AND, OR, NOT)."""

    def test_search_and_operator(self, search_repo, indexed_episode):
        """Test AND operator (implicit in FTS5)."""
        response = search_repo.search("artificial intelligence")

        assert response.total > 0

    def test_search_or_operator(self, search_repo, indexed_episode):
        """Test OR operator."""
        response = search_repo.search("robots OR neural")

        assert response.total > 0

    def test_search_not_operator(self, search_repo, indexed_episode):
        """Test NOT operator."""
        # Search for "learning" but NOT in "machine learning" context
        response = search_repo.search("learning NOT machine")

        # Should still find "deep learning"
        assert response.total > 0


class TestSearchWithFeedFilter:
    """Tests for feed filtering."""

    def test_search_with_feed_filter(self, search_repo, indexed_episode, sample_feed):
        """Test search filtered by feed ID."""
        episode, _ = indexed_episode

        response = search_repo.search("podcast", feed_id=sample_feed.id)

        assert response.total > 0
        assert all(r.feed_id == sample_feed.id for r in response.results)

    def test_search_with_nonexistent_feed(self, search_repo, indexed_episode):
        """Test search with non-matching feed returns no results."""
        response = search_repo.search("podcast", feed_id=99999)

        assert response.total == 0


class TestSearchInvalidQuery:
    """Tests for handling invalid queries."""

    def test_search_invalid_fts_syntax(self, search_repo, indexed_episode):
        """Test invalid FTS5 syntax returns empty response (not exception)."""
        # Unbalanced quotes are invalid FTS5 syntax
        response = search_repo.search('"unclosed quote')

        # Should return empty response, not raise exception
        assert response.total == 0
        assert len(response.results) == 0

    def test_search_special_characters(self, search_repo, indexed_episode):
        """Test queries with special characters are handled."""
        # These could cause issues in FTS5
        response = search_repo.search("test@email.com")

        # Should not raise exception
        assert isinstance(response.total, int)


class TestSearchEpisode:
    """Tests for searching within a specific episode."""

    def test_search_episode_finds_segments(self, search_repo, indexed_episode):
        """Test searching within an episode returns matching segments."""
        episode, _ = indexed_episode

        results = search_repo.search_episode(episode.id, "AI")

        assert len(results) > 0
        assert all(r.episode_id == episode.id for r in results)

    def test_search_episode_no_matches(self, search_repo, indexed_episode):
        """Test search in episode with no matches returns empty list."""
        episode, _ = indexed_episode

        results = search_repo.search_episode(episode.id, "xyznonexistent")

        assert len(results) == 0

    def test_search_episode_invalid_id(self, search_repo, indexed_episode):
        """Test search with invalid episode ID returns empty list."""
        results = search_repo.search_episode(99999, "podcast")

        assert len(results) == 0


class TestRemoveEpisode:
    """Tests for remove_episode method."""

    def test_remove_episode_clears_segments(self, search_repo, indexed_episode):
        """Test removing episode removes all its segments."""
        episode, segment_count = indexed_episode

        # Verify segments exist
        assert search_repo.get_indexed_count() == segment_count

        # Remove
        removed = search_repo.remove_episode(episode.id)

        assert removed == segment_count
        assert search_repo.get_indexed_count() == 0

    def test_remove_nonexistent_episode(self, search_repo):
        """Test removing non-indexed episode returns 0."""
        removed = search_repo.remove_episode(99999)

        assert removed == 0


class TestGetIndexedCount:
    """Tests for get_indexed_count and get_indexed_episodes."""

    def test_get_indexed_count(self, search_repo, indexed_episode):
        """Test counting indexed segments."""
        _, segment_count = indexed_episode

        count = search_repo.get_indexed_count()

        assert count == segment_count

    def test_get_indexed_episodes(self, search_repo, indexed_episode):
        """Test getting set of indexed episode IDs."""
        episode, _ = indexed_episode

        indexed = search_repo.get_indexed_episodes()

        assert episode.id in indexed
        assert len(indexed) == 1
