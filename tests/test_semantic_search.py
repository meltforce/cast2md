"""Tests for semantic search functionality."""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from cast2md.search.embeddings import (
    text_hash,
    is_embeddings_available,
    DEFAULT_MODEL_NAME,
    EMBEDDING_DIM,
)
from cast2md.search.repository import TranscriptSearchRepository


@pytest.fixture
def transcript_file():
    """Create a temporary transcript file with known content."""
    content = """# Episode Transcript

*Language: en (100.0% confidence)*

**[00:00]** Hello and welcome to the fitness podcast. Today we're discussing protein and muscle building.

**[00:30]** Our guest is a nutrition expert who studies strength training and weightlifting.

**[01:00]** Let's talk about how protein helps build muscle. The science is fascinating.

**[01:30]** Recovery is also important. Sleep and rest help your muscles grow after exercise.
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(content)
        f.flush()
        yield Path(f.name)
    Path(f.name).unlink(missing_ok=True)


class TestTextHash:
    """Tests for text_hash function."""

    def test_text_hash_returns_string(self):
        """Test that text_hash returns a string."""
        result = text_hash("hello world")
        assert isinstance(result, str)

    def test_text_hash_consistent(self):
        """Test that same input produces same hash."""
        text = "test content"
        assert text_hash(text) == text_hash(text)

    def test_text_hash_different_input(self):
        """Test that different input produces different hash."""
        assert text_hash("hello") != text_hash("world")

    def test_text_hash_length(self):
        """Test that hash is 16 characters."""
        result = text_hash("any text")
        assert len(result) == 16


class TestEmbeddingsAvailable:
    """Tests for is_embeddings_available function."""

    def test_is_embeddings_available_returns_bool(self):
        """Test that is_embeddings_available returns a boolean."""
        result = is_embeddings_available()
        assert isinstance(result, bool)


class TestEmbeddingsConstants:
    """Tests for embedding constants."""

    def test_default_model_name(self):
        """Test default model name is set."""
        assert DEFAULT_MODEL_NAME == "paraphrase-multilingual-MiniLM-L12-v2"

    def test_embedding_dim(self):
        """Test embedding dimension is correct for the model."""
        assert EMBEDDING_DIM == 384


class TestHybridSearchKeywordOnly:
    """Tests for hybrid_search in keyword-only mode (no embeddings required)."""

    def test_hybrid_search_keyword_mode_returns_results(
        self, db_conn, sample_episode, search_repo, transcript_file
    ):
        """Test hybrid_search in keyword mode returns results."""
        # Index the transcript
        cursor = db_conn.cursor()
        cursor.execute(
            "UPDATE episode SET transcript_path = %s, status = 'completed' WHERE id = %s",
            (str(transcript_file), sample_episode.id),
        )
        db_conn.commit()
        search_repo.index_episode(sample_episode.id, str(transcript_file))

        # Search in keyword mode
        response = search_repo.hybrid_search("protein", mode="keyword")

        assert response.total > 0
        assert response.mode == "keyword"
        assert len(response.results) > 0
        assert response.results[0].match_type == "keyword"

    def test_hybrid_search_keyword_no_results(
        self, db_conn, sample_episode, search_repo, transcript_file
    ):
        """Test hybrid_search in keyword mode with no matches."""
        cursor = db_conn.cursor()
        cursor.execute(
            "UPDATE episode SET transcript_path = %s, status = 'completed' WHERE id = %s",
            (str(transcript_file), sample_episode.id),
        )
        db_conn.commit()
        search_repo.index_episode(sample_episode.id, str(transcript_file))

        response = search_repo.hybrid_search("xyznonexistent123", mode="keyword")

        assert response.total == 0
        assert len(response.results) == 0

    def test_hybrid_search_empty_query(self, search_repo):
        """Test hybrid_search with empty query."""
        response = search_repo.hybrid_search("")

        assert response.total == 0
        assert len(response.results) == 0

    def test_hybrid_search_feed_filter(
        self, db_conn, sample_episode, sample_feed, search_repo, transcript_file
    ):
        """Test hybrid_search with feed filter."""
        cursor = db_conn.cursor()
        cursor.execute(
            "UPDATE episode SET transcript_path = %s, status = 'completed' WHERE id = %s",
            (str(transcript_file), sample_episode.id),
        )
        db_conn.commit()
        search_repo.index_episode(sample_episode.id, str(transcript_file))

        # Search with correct feed ID
        response = search_repo.hybrid_search(
            "protein", feed_id=sample_feed.id, mode="keyword"
        )
        assert response.total > 0

        # Search with wrong feed ID
        response = search_repo.hybrid_search("protein", feed_id=99999, mode="keyword")
        assert response.total == 0


class TestHybridSearchResultStructure:
    """Tests for hybrid search result structure."""

    def test_result_has_required_fields(
        self, db_conn, sample_episode, search_repo, transcript_file
    ):
        """Test that search results have all required fields."""
        cursor = db_conn.cursor()
        cursor.execute(
            "UPDATE episode SET transcript_path = %s, status = 'completed' WHERE id = %s",
            (str(transcript_file), sample_episode.id),
        )
        db_conn.commit()
        search_repo.index_episode(sample_episode.id, str(transcript_file))

        response = search_repo.hybrid_search("muscle", mode="keyword")

        assert len(response.results) > 0
        result = response.results[0]

        # Check all required fields exist
        assert hasattr(result, "episode_id")
        assert hasattr(result, "episode_title")
        assert hasattr(result, "feed_id")
        assert hasattr(result, "feed_title")
        assert hasattr(result, "segment_start")
        assert hasattr(result, "segment_end")
        assert hasattr(result, "text")
        assert hasattr(result, "score")
        assert hasattr(result, "match_type")

    def test_result_score_is_positive(
        self, db_conn, sample_episode, search_repo, transcript_file
    ):
        """Test that RRF scores are positive."""
        cursor = db_conn.cursor()
        cursor.execute(
            "UPDATE episode SET transcript_path = %s, status = 'completed' WHERE id = %s",
            (str(transcript_file), sample_episode.id),
        )
        db_conn.commit()
        search_repo.index_episode(sample_episode.id, str(transcript_file))

        response = search_repo.hybrid_search("fitness", mode="keyword")

        for result in response.results:
            assert result.score > 0


class TestEmbeddingIndexing:
    """Tests for embedding indexing (mocked, since sentence-transformers may not be available)."""

    def test_get_embedded_episodes_empty(self, search_repo):
        """Test get_embedded_episodes returns empty set initially."""
        result = search_repo.get_embedded_episodes()
        assert result == set()

    def test_get_embedding_count_zero(self, search_repo):
        """Test get_embedding_count returns 0 initially."""
        result = search_repo.get_embedding_count()
        assert result == 0


class TestHybridSearchModes:
    """Tests for different hybrid search modes."""

    def test_mode_hybrid(self, db_conn, sample_episode, search_repo, transcript_file):
        """Test hybrid mode is accepted."""
        cursor = db_conn.cursor()
        cursor.execute(
            "UPDATE episode SET transcript_path = %s, status = 'completed' WHERE id = %s",
            (str(transcript_file), sample_episode.id),
        )
        db_conn.commit()
        search_repo.index_episode(sample_episode.id, str(transcript_file))

        response = search_repo.hybrid_search("protein", mode="hybrid")
        assert response.mode == "hybrid"

    def test_mode_semantic(self, db_conn, sample_episode, search_repo, transcript_file):
        """Test semantic mode is accepted."""
        cursor = db_conn.cursor()
        cursor.execute(
            "UPDATE episode SET transcript_path = %s, status = 'completed' WHERE id = %s",
            (str(transcript_file), sample_episode.id),
        )
        db_conn.commit()
        search_repo.index_episode(sample_episode.id, str(transcript_file))

        response = search_repo.hybrid_search("protein", mode="semantic")
        assert response.mode == "semantic"

    def test_mode_keyword(self, db_conn, sample_episode, search_repo, transcript_file):
        """Test keyword mode is accepted."""
        cursor = db_conn.cursor()
        cursor.execute(
            "UPDATE episode SET transcript_path = %s, status = 'completed' WHERE id = %s",
            (str(transcript_file), sample_episode.id),
        )
        db_conn.commit()
        search_repo.index_episode(sample_episode.id, str(transcript_file))

        response = search_repo.hybrid_search("protein", mode="keyword")
        assert response.mode == "keyword"


@pytest.mark.skipif(
    not is_embeddings_available(),
    reason="sentence-transformers not installed"
)
class TestEmbeddingGeneration:
    """Tests for actual embedding generation (requires sentence-transformers)."""

    def test_generate_embedding_returns_bytes(self):
        """Test that generate_embedding returns bytes."""
        from cast2md.search.embeddings import generate_embedding

        result = generate_embedding("test text")
        assert isinstance(result, bytes)

    def test_generate_embedding_correct_size(self):
        """Test that embedding has correct size (384 floats * 4 bytes)."""
        from cast2md.search.embeddings import generate_embedding

        result = generate_embedding("test text")
        expected_size = EMBEDDING_DIM * 4  # 384 floats * 4 bytes each
        assert len(result) == expected_size

    def test_generate_embeddings_batch(self):
        """Test batch embedding generation."""
        from cast2md.search.embeddings import generate_embeddings_batch

        texts = ["hello world", "test content", "another text"]
        results = generate_embeddings_batch(texts)

        assert len(results) == 3
        for result in results:
            assert isinstance(result, bytes)
            assert len(result) == EMBEDDING_DIM * 4

    def test_generate_embeddings_batch_empty(self):
        """Test batch embedding with empty list."""
        from cast2md.search.embeddings import generate_embeddings_batch

        results = generate_embeddings_batch([])
        assert results == []

    def test_embedding_to_floats(self):
        """Test converting embedding bytes back to floats."""
        from cast2md.search.embeddings import generate_embedding, embedding_to_floats

        embedding = generate_embedding("test")
        floats = embedding_to_floats(embedding)

        assert len(floats) == EMBEDDING_DIM
        assert all(isinstance(f, float) for f in floats)
