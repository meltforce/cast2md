"""Repository for transcript full-text search operations."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional

from cast2md.db.sql import execute
from cast2md.search.parser import merge_word_level_segments, parse_transcript_file

logger = logging.getLogger(__name__)

# Type alias for database connection
Connection = Any


@dataclass
class SearchResult:
    """A search result with episode info and matching segment."""

    episode_id: int
    episode_title: str
    feed_id: int
    feed_title: str
    published_at: Optional[str]
    segment_start: float
    segment_end: float
    snippet: str
    rank: float


@dataclass
class SearchResponse:
    """Response from a transcript search query."""

    query: str
    total: int
    results: list[SearchResult]


@dataclass
class HybridSearchResult:
    """A result from hybrid (keyword + semantic) search."""

    episode_id: int
    episode_title: str
    feed_id: int
    feed_title: str
    published_at: Optional[str]
    segment_start: float
    segment_end: float
    text: str
    score: float  # Combined RRF score (higher is better)
    match_type: str  # "keyword", "semantic", or "both"


@dataclass
class HybridSearchResponse:
    """Response from hybrid search."""

    query: str
    total: int
    mode: str  # "hybrid", "semantic", or "keyword"
    results: list[HybridSearchResult]


class TranscriptSearchRepository:
    """Repository for transcript full-text search operations."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def index_episode(self, episode_id: int, transcript_path: str) -> int:
        """Index a transcript into full-text search.

        Args:
            episode_id: Episode ID to index.
            transcript_path: Path to transcript markdown file.

        Returns:
            Number of segments indexed.
        """
        path = Path(transcript_path)
        if not path.exists():
            return 0

        # Remove existing segments for this episode
        execute(self.conn, "DELETE FROM transcript_segments WHERE episode_id = %s", (episode_id,))

        # Parse transcript and merge word-level segments into phrases
        segments = parse_transcript_file(path)
        segments = merge_word_level_segments(segments)

        # Insert segments
        for segment in segments:
            execute(
                self.conn,
                """
                INSERT INTO transcript_segments (episode_id, segment_start, segment_end, text)
                VALUES (%s, %s, %s, %s)
                """,
                (episode_id, segment.start, segment.end, segment.text),
            )

        self.conn.commit()
        return len(segments)

    def remove_episode(self, episode_id: int) -> int:
        """Remove all indexed segments for an episode.

        Args:
            episode_id: Episode ID to remove.

        Returns:
            Number of segments removed.
        """
        cursor = execute(self.conn, "DELETE FROM transcript_segments WHERE episode_id = %s", (episode_id,))
        self.conn.commit()
        return cursor.rowcount

    def search(
        self,
        query: str,
        feed_id: Optional[int] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> SearchResponse:
        """Search transcripts using full-text search.

        Args:
            query: Search query.
            feed_id: Optional feed ID to filter results.
            limit: Maximum results to return.
            offset: Offset for pagination.

        Returns:
            SearchResponse with results and total count.
        """
        safe_query = query.strip()
        if not safe_query:
            return SearchResponse(query=query, total=0, results=[])

        try:
            # Build the query
            if feed_id is not None:
                count_sql = """
                    SELECT COUNT(DISTINCT t.episode_id)
                    FROM transcript_segments t
                    JOIN episode e ON t.episode_id = e.id
                    WHERE t.text_search @@ plainto_tsquery('english', %s)
                      AND e.feed_id = %s
                """
                count_params = (safe_query, feed_id)

                results_sql = """
                    SELECT
                        t.episode_id,
                        e.title as episode_title,
                        e.feed_id,
                        COALESCE(f.custom_title, f.title) as feed_title,
                        e.published_at,
                        t.segment_start,
                        t.segment_end,
                        ts_headline('english', t.text, plainto_tsquery('english', %s),
                                   'StartSel=<mark>, StopSel=</mark>, MaxFragments=1, MaxWords=32') as snippet,
                        ts_rank(t.text_search, plainto_tsquery('english', %s)) as rank
                    FROM transcript_segments t
                    JOIN episode e ON t.episode_id = e.id
                    JOIN feed f ON e.feed_id = f.id
                    WHERE t.text_search @@ plainto_tsquery('english', %s)
                      AND e.feed_id = %s
                    ORDER BY rank DESC
                    LIMIT %s OFFSET %s
                """
                results_params = (safe_query, safe_query, safe_query, feed_id, limit, offset)
            else:
                count_sql = """
                    SELECT COUNT(DISTINCT t.episode_id)
                    FROM transcript_segments t
                    WHERE t.text_search @@ plainto_tsquery('english', %s)
                """
                count_params = (safe_query,)

                results_sql = """
                    SELECT
                        t.episode_id,
                        e.title as episode_title,
                        e.feed_id,
                        COALESCE(f.custom_title, f.title) as feed_title,
                        e.published_at,
                        t.segment_start,
                        t.segment_end,
                        ts_headline('english', t.text, plainto_tsquery('english', %s),
                                   'StartSel=<mark>, StopSel=</mark>, MaxFragments=1, MaxWords=32') as snippet,
                        ts_rank(t.text_search, plainto_tsquery('english', %s)) as rank
                    FROM transcript_segments t
                    JOIN episode e ON t.episode_id = e.id
                    JOIN feed f ON e.feed_id = f.id
                    WHERE t.text_search @@ plainto_tsquery('english', %s)
                    ORDER BY rank DESC
                    LIMIT %s OFFSET %s
                """
                results_params = (safe_query, safe_query, safe_query, limit, offset)

            cursor = self.conn.cursor()
            cursor.execute(count_sql, count_params)
            total = cursor.fetchone()[0]

            cursor.execute(results_sql, results_params)
            results = [
                SearchResult(
                    episode_id=row[0],
                    episode_title=row[1],
                    feed_id=row[2],
                    feed_title=row[3],
                    published_at=row[4],
                    segment_start=row[5],
                    segment_end=row[6],
                    snippet=row[7],
                    rank=row[8],
                )
                for row in cursor.fetchall()
            ]

            return SearchResponse(query=query, total=total, results=results)
        except Exception as e:
            logger.warning(f"Search failed: {e}")
            return SearchResponse(query=query, total=0, results=[])

    def search_episode(
        self,
        episode_id: int,
        query: str,
        limit: int = 50,
    ) -> list[SearchResult]:
        """Search within a specific episode's transcript.

        Args:
            episode_id: Episode ID to search within.
            query: Search query.
            limit: Maximum results.

        Returns:
            List of matching segments.
        """
        sql = """
            SELECT
                t.episode_id,
                e.title as episode_title,
                e.feed_id,
                COALESCE(f.custom_title, f.title) as feed_title,
                e.published_at,
                t.segment_start,
                t.segment_end,
                ts_headline('english', t.text, plainto_tsquery('english', %s),
                           'StartSel=<mark>, StopSel=</mark>, MaxFragments=1, MaxWords=32') as snippet,
                ts_rank(t.text_search, plainto_tsquery('english', %s)) as rank
            FROM transcript_segments t
            JOIN episode e ON t.episode_id = e.id
            JOIN feed f ON e.feed_id = f.id
            WHERE t.text_search @@ plainto_tsquery('english', %s) AND t.episode_id = %s
            ORDER BY t.segment_start
            LIMIT %s
        """

        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, (query, query, query, episode_id, limit))
            return [
                SearchResult(
                    episode_id=row[0],
                    episode_title=row[1],
                    feed_id=row[2],
                    feed_title=row[3],
                    published_at=row[4],
                    segment_start=row[5],
                    segment_end=row[6],
                    snippet=row[7],
                    rank=row[8],
                )
                for row in cursor.fetchall()
            ]
        except Exception:
            return []

    def get_indexed_count(self) -> int:
        """Get the total number of indexed segments."""
        try:
            cursor = execute(self.conn, "SELECT COUNT(*) FROM transcript_segments", ())
            return cursor.fetchone()[0]
        except Exception:
            return 0

    def get_indexed_episodes(self) -> set[int]:
        """Get set of episode IDs that have been indexed."""
        try:
            cursor = execute(
                self.conn, "SELECT DISTINCT episode_id FROM transcript_segments", ()
            )
            return {row[0] for row in cursor.fetchall()}
        except Exception:
            return set()

    def reindex_all(self, episode_transcripts: dict[int, str]) -> tuple[int, int]:
        """Reindex all transcripts.

        Args:
            episode_transcripts: Dict mapping episode_id to transcript_path.

        Returns:
            Tuple of (episodes_indexed, segments_indexed).
        """
        # Clear existing index
        execute(self.conn, "DELETE FROM transcript_segments", ())
        self.conn.commit()

        episodes_indexed = 0
        segments_indexed = 0

        for episode_id, transcript_path in episode_transcripts.items():
            count = self.index_episode(episode_id, transcript_path)
            if count > 0:
                episodes_indexed += 1
                segments_indexed += count

        return episodes_indexed, segments_indexed

    def index_episode_embeddings(
        self, episode_id: int, transcript_path: str, model_name: str | None = None
    ) -> int:
        """Generate and store embeddings for a transcript's segments.

        Args:
            episode_id: Episode ID to index.
            transcript_path: Path to transcript markdown file.
            model_name: Embedding model name (defaults to configured model).

        Returns:
            Number of segments embedded.
        """
        from cast2md.db.repository import EpisodeRepository
        from cast2md.search.embeddings import (
            DEFAULT_MODEL_NAME,
            generate_embeddings_batch,
            text_hash,
        )

        if model_name is None:
            model_name = DEFAULT_MODEL_NAME

        path = Path(transcript_path)
        if not path.exists():
            return 0

        # Parse transcript and merge word-level segments into phrases
        segments = parse_transcript_file(path)
        segments = merge_word_level_segments(segments)
        if not segments:
            return 0

        # Get feed_id for the episode
        episode_repo = EpisodeRepository(self.conn)
        episode = episode_repo.get_by_id(episode_id)
        if not episode:
            return 0
        feed_id = episode.feed_id

        # Remove existing embeddings for this episode
        cursor = self.conn.cursor()
        cursor.execute(
            "DELETE FROM segment_embeddings WHERE episode_id = %s",
            (episode_id,),
        )

        # Generate embeddings in batch (numpy arrays for PostgreSQL)
        texts = [seg.text for seg in segments]
        embeddings = generate_embeddings_batch(texts, model_name, as_numpy=True)

        # Insert embeddings
        for segment, embedding in zip(segments, embeddings):
            cursor.execute(
                """
                INSERT INTO segment_embeddings
                (episode_id, feed_id, segment_start, segment_end, text_hash, model_name, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (episode_id, segment_start, segment_end)
                DO UPDATE SET embedding = EXCLUDED.embedding, text_hash = EXCLUDED.text_hash
                """,
                (
                    episode_id,
                    feed_id,
                    float(segment.start),
                    float(segment.end),
                    text_hash(segment.text),
                    model_name,
                    embedding,
                ),
            )

        self.conn.commit()
        return len(segments)

    def remove_episode_embeddings(self, episode_id: int) -> int:
        """Remove all embeddings for an episode.

        Args:
            episode_id: Episode ID to remove.

        Returns:
            Number of embeddings removed.
        """
        cursor = execute(
            self.conn,
            "DELETE FROM segment_embeddings WHERE episode_id = %s",
            (episode_id,),
        )
        self.conn.commit()
        return cursor.rowcount

    def get_embedded_episodes(self) -> set[int]:
        """Get set of episode IDs that have embeddings."""
        try:
            cursor = execute(
                self.conn, "SELECT DISTINCT episode_id FROM segment_embeddings", ()
            )
            return {row[0] for row in cursor.fetchall()}
        except Exception:
            # Table doesn't exist (embeddings not available)
            return set()

    def get_embedding_count(self) -> int:
        """Get total number of segment embeddings."""
        try:
            cursor = execute(self.conn, "SELECT COUNT(*) FROM segment_embeddings", ())
            return cursor.fetchone()[0]
        except Exception:
            # Table doesn't exist (embeddings not available)
            return 0

    def store_embeddings_from_node(
        self, episode_id: int, embeddings: list[dict]
    ) -> int:
        """Store embeddings received from a remote node.

        Args:
            episode_id: Episode ID the embeddings belong to.
            embeddings: List of dicts with keys:
                - segment_index: Index in the segment list
                - text: Segment text
                - start: Segment start time
                - end: Segment end time
                - embedding: List of floats (the embedding vector)

        Returns:
            Number of embeddings stored.
        """
        import numpy as np

        from cast2md.db.repository import EpisodeRepository
        from cast2md.search.embeddings import DEFAULT_MODEL_NAME, text_hash

        if not embeddings:
            return 0

        # Get feed_id for the episode
        episode_repo = EpisodeRepository(self.conn)
        episode = episode_repo.get_by_id(episode_id)
        if not episode:
            raise ValueError(f"Episode {episode_id} not found")
        feed_id = episode.feed_id

        # Remove existing embeddings for this episode
        cursor = self.conn.cursor()
        cursor.execute(
            "DELETE FROM segment_embeddings WHERE episode_id = %s",
            (episode_id,),
        )

        # Insert embeddings
        for emb in embeddings:
            # Convert embedding list to numpy array for pgvector
            embedding_array = np.array(emb["embedding"], dtype=np.float32)

            cursor.execute(
                """
                INSERT INTO segment_embeddings
                (episode_id, feed_id, segment_start, segment_end, text_hash, model_name, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (episode_id, segment_start, segment_end)
                DO UPDATE SET embedding = EXCLUDED.embedding, text_hash = EXCLUDED.text_hash
                """,
                (
                    episode_id,
                    feed_id,
                    float(emb["start"]),
                    float(emb["end"]),
                    text_hash(emb["text"]),
                    DEFAULT_MODEL_NAME,
                    embedding_array,
                ),
            )

        self.conn.commit()
        return len(embeddings)

    def _vector_search(
        self,
        query_embedding,
        feed_id: int | None = None,
        limit: int = 50,
    ) -> list[tuple]:
        """Perform vector similarity search using pgvector.

        Args:
            query_embedding: Query embedding (numpy array or list).
            feed_id: Optional feed ID to filter results.
            limit: Maximum results to return.

        Returns:
            List of tuples with segment info and distance.
        """
        import struct

        import numpy as np

        # Convert bytes to list if needed
        if isinstance(query_embedding, bytes):
            count = len(query_embedding) // 4
            query_embedding = list(struct.unpack(f"{count}f", query_embedding))

        # Convert to numpy array for pgvector compatibility
        query_embedding = np.array(query_embedding, dtype=np.float32)

        cursor = self.conn.cursor()

        if feed_id is not None:
            cursor.execute(
                """
                SELECT
                    se.episode_id,
                    e.title as episode_title,
                    se.feed_id,
                    COALESCE(f.custom_title, f.title) as feed_title,
                    e.published_at,
                    se.segment_start,
                    se.segment_end,
                    ts.text,
                    se.embedding <=> %s as distance
                FROM segment_embeddings se
                JOIN episode e ON se.episode_id = e.id
                JOIN feed f ON se.feed_id = f.id
                LEFT JOIN transcript_segments ts ON
                    ts.episode_id = se.episode_id AND
                    ts.segment_start = se.segment_start AND
                    ts.segment_end = se.segment_end
                WHERE se.feed_id = %s
                ORDER BY se.embedding <=> %s
                LIMIT %s
                """,
                (query_embedding, feed_id, query_embedding, limit),
            )
        else:
            cursor.execute(
                """
                SELECT
                    se.episode_id,
                    e.title as episode_title,
                    se.feed_id,
                    COALESCE(f.custom_title, f.title) as feed_title,
                    e.published_at,
                    se.segment_start,
                    se.segment_end,
                    ts.text,
                    se.embedding <=> %s as distance
                FROM segment_embeddings se
                JOIN episode e ON se.episode_id = e.id
                JOIN feed f ON se.feed_id = f.id
                LEFT JOIN transcript_segments ts ON
                    ts.episode_id = se.episode_id AND
                    ts.segment_start = se.segment_start AND
                    ts.segment_end = se.segment_end
                ORDER BY se.embedding <=> %s
                LIMIT %s
                """,
                (query_embedding, query_embedding, limit),
            )

        return cursor.fetchall()

    def hybrid_search(
        self,
        query: str,
        feed_id: int | None = None,
        limit: int = 20,
        offset: int = 0,
        mode: Literal["hybrid", "semantic", "keyword"] = "hybrid",
    ) -> HybridSearchResponse:
        """Perform hybrid search combining keyword and semantic search.

        Uses Reciprocal Rank Fusion (RRF) to combine results from
        keyword search and vector similarity search.

        Args:
            query: Search query.
            feed_id: Optional feed ID to filter results.
            limit: Maximum results to return.
            offset: Offset for pagination.
            mode: Search mode - "hybrid", "semantic", or "keyword".

        Returns:
            HybridSearchResponse with combined results.
        """
        import time

        from cast2md.db.connection import is_pgvector_available
        from cast2md.search.embeddings import generate_embedding, is_embeddings_available

        safe_query = query.strip()
        if not safe_query:
            return HybridSearchResponse(query=query, total=0, mode=mode, results=[])

        t_start = time.perf_counter()

        # RRF constant (standard value from literature)
        K = 60

        # Dictionary to collect results: key = (episode_id, segment_start, segment_end)
        results_map: dict[tuple, dict] = {}

        # Keyword search (FTS)
        if mode in ("hybrid", "keyword"):
            t_keyword_start = time.perf_counter()
            try:
                keyword_response = self.search(
                    query=safe_query,
                    feed_id=feed_id,
                    limit=limit * 2,  # Get more results for fusion
                )
                t_keyword_end = time.perf_counter()
                logger.info(f"[TIMING] Keyword search: {t_keyword_end - t_keyword_start:.3f}s")
                for rank, result in enumerate(keyword_response.results):
                    key = (result.episode_id, result.segment_start, result.segment_end)
                    rrf_score = 1.0 / (K + rank + 1)

                    if key in results_map:
                        results_map[key]["keyword_rank"] = rank
                        results_map[key]["rrf_score"] += rrf_score
                        results_map[key]["match_type"] = "both"
                    else:
                        results_map[key] = {
                            "episode_id": result.episode_id,
                            "episode_title": result.episode_title,
                            "feed_id": result.feed_id,
                            "feed_title": result.feed_title,
                            "published_at": result.published_at,
                            "segment_start": result.segment_start,
                            "segment_end": result.segment_end,
                            "text": result.snippet,
                            "keyword_rank": rank,
                            "semantic_rank": None,
                            "rrf_score": rrf_score,
                            "match_type": "keyword",
                        }
            except Exception as e:
                logger.warning(f"Keyword search failed: {e}")

        # Semantic search (vector similarity with pgvector)
        if mode in ("hybrid", "semantic") and is_embeddings_available() and is_pgvector_available():
            try:
                t_embed_start = time.perf_counter()
                query_embedding = generate_embedding(safe_query)
                t_embed_end = time.perf_counter()
                logger.info(f"[TIMING] Query embedding: {t_embed_end - t_embed_start:.3f}s")

                t_vector_start = time.perf_counter()
                vector_results = self._vector_search(
                    query_embedding=query_embedding,
                    feed_id=feed_id,
                    limit=limit * 2,
                )
                t_vector_end = time.perf_counter()
                logger.info(f"[TIMING] Vector search: {t_vector_end - t_vector_start:.3f}s")

                for rank, row in enumerate(vector_results):
                    key = (row[0], row[5], row[6])  # episode_id, segment_start, segment_end
                    rrf_score = 1.0 / (K + rank + 1)

                    if key in results_map:
                        results_map[key]["semantic_rank"] = rank
                        results_map[key]["rrf_score"] += rrf_score
                        if results_map[key]["match_type"] == "keyword":
                            results_map[key]["match_type"] = "both"
                    else:
                        results_map[key] = {
                            "episode_id": row[0],
                            "episode_title": row[1],
                            "feed_id": row[2],
                            "feed_title": row[3],
                            "published_at": row[4],
                            "segment_start": row[5],
                            "segment_end": row[6],
                            "text": row[7] or "",
                            "keyword_rank": None,
                            "semantic_rank": rank,
                            "rrf_score": rrf_score,
                            "match_type": "semantic",
                        }
            except Exception as e:
                logger.warning(f"Semantic search failed: {e}")

        # Sort by RRF score (descending - higher is better)
        sorted_results = sorted(
            results_map.values(),
            key=lambda x: x["rrf_score"],
            reverse=True,
        )

        # Apply pagination (offset + limit)
        limited = sorted_results[offset:offset + limit]

        results = [
            HybridSearchResult(
                episode_id=r["episode_id"],
                episode_title=r["episode_title"],
                feed_id=r["feed_id"],
                feed_title=r["feed_title"],
                published_at=r["published_at"],
                segment_start=r["segment_start"],
                segment_end=r["segment_end"],
                text=r["text"],
                score=r["rrf_score"],
                match_type=r["match_type"],
            )
            for r in limited
        ]

        t_end = time.perf_counter()
        logger.info(f"[TIMING] Total hybrid_search: {t_end - t_start:.3f}s")

        return HybridSearchResponse(
            query=query,
            total=len(sorted_results),
            mode=mode,
            results=results,
        )
