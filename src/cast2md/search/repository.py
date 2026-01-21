"""Repository for transcript full-text search operations."""

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from cast2md.search.parser import parse_transcript_file

logger = logging.getLogger(__name__)


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
class UnifiedSearchResult:
    """A unified search result grouping matches by episode."""

    episode_id: int
    episode_title: str
    feed_id: int
    feed_title: str
    published_at: Optional[str]
    match_sources: list[str]  # e.g., ["title", "description", "transcript"]
    transcript_match_count: int
    best_rank: float  # Best (lowest) BM25 rank across matches


@dataclass
class UnifiedSearchResponse:
    """Response from unified search."""

    query: str
    total: int
    results: list[UnifiedSearchResult]


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
    """Repository for transcript FTS5 search operations."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def index_episode(self, episode_id: int, transcript_path: str) -> int:
        """Index a transcript into FTS5.

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
        self.conn.execute(
            "DELETE FROM transcript_fts WHERE episode_id = ?",
            (episode_id,),
        )

        # Parse transcript
        segments = parse_transcript_file(path)

        # Insert segments
        for segment in segments:
            self.conn.execute(
                """
                INSERT INTO transcript_fts (text, episode_id, segment_start, segment_end)
                VALUES (?, ?, ?, ?)
                """,
                (segment.text, episode_id, segment.start, segment.end),
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
        cursor = self.conn.execute(
            "DELETE FROM transcript_fts WHERE episode_id = ?",
            (episode_id,),
        )
        self.conn.commit()
        return cursor.rowcount

    def search(
        self,
        query: str,
        feed_id: Optional[int] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> SearchResponse:
        """Search transcripts using FTS5.

        Args:
            query: Search query (supports FTS5 syntax: phrases, AND, OR, NOT).
            feed_id: Optional feed ID to filter results.
            limit: Maximum results to return.
            offset: Offset for pagination.

        Returns:
            SearchResponse with results and total count.
        """
        # Build the query - escape special FTS5 characters for safety
        # Users can still use AND, OR, NOT, quotes for phrases
        safe_query = query.strip()
        if not safe_query:
            return SearchResponse(query=query, total=0, results=[])

        # Base query with joins to get episode and feed info
        base_sql = """
            FROM transcript_fts t
            JOIN episode e ON t.episode_id = e.id
            JOIN feed f ON e.feed_id = f.id
            WHERE t.text MATCH ?
        """
        params: list = [safe_query]

        # Add feed filter if specified
        if feed_id is not None:
            base_sql += " AND e.feed_id = ?"
            params.append(feed_id)

        # Get total count
        count_sql = f"SELECT COUNT(DISTINCT t.episode_id) {base_sql}"
        try:
            count_cursor = self.conn.execute(count_sql, params)
            total = count_cursor.fetchone()[0]
        except sqlite3.OperationalError:
            # Invalid FTS5 query syntax
            return SearchResponse(query=query, total=0, results=[])

        # Get results with snippets and ranking
        # Note: snippet() and bm25() require the actual table name, not alias
        results_sql = f"""
            SELECT
                t.episode_id,
                e.title as episode_title,
                e.feed_id,
                COALESCE(f.custom_title, f.title) as feed_title,
                e.published_at,
                t.segment_start,
                t.segment_end,
                snippet(transcript_fts, 0, '<mark>', '</mark>', '...', 32) as snippet,
                bm25(transcript_fts) as rank
            {base_sql}
            ORDER BY rank
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        try:
            cursor = self.conn.execute(results_sql, params)
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
        except sqlite3.OperationalError:
            # Invalid FTS5 query syntax
            return SearchResponse(query=query, total=0, results=[])

        return SearchResponse(query=query, total=total, results=results)

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
        # Note: snippet() and bm25() require the actual table name, not alias
        sql = """
            SELECT
                t.episode_id,
                e.title as episode_title,
                e.feed_id,
                COALESCE(f.custom_title, f.title) as feed_title,
                e.published_at,
                t.segment_start,
                t.segment_end,
                snippet(transcript_fts, 0, '<mark>', '</mark>', '...', 32) as snippet,
                bm25(transcript_fts) as rank
            FROM transcript_fts t
            JOIN episode e ON t.episode_id = e.id
            JOIN feed f ON e.feed_id = f.id
            WHERE t.text MATCH ? AND t.episode_id = ?
            ORDER BY t.segment_start
            LIMIT ?
        """

        try:
            cursor = self.conn.execute(sql, (query, episode_id, limit))
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
        except sqlite3.OperationalError:
            return []

    def get_indexed_count(self) -> int:
        """Get the total number of indexed segments."""
        cursor = self.conn.execute("SELECT COUNT(*) FROM transcript_fts")
        return cursor.fetchone()[0]

    def get_indexed_episodes(self) -> set[int]:
        """Get set of episode IDs that have been indexed."""
        cursor = self.conn.execute(
            "SELECT DISTINCT episode_id FROM transcript_fts"
        )
        return {row[0] for row in cursor.fetchall()}

    def reindex_all(self, episode_transcripts: dict[int, str]) -> tuple[int, int]:
        """Reindex all transcripts.

        Args:
            episode_transcripts: Dict mapping episode_id to transcript_path.

        Returns:
            Tuple of (episodes_indexed, segments_indexed).
        """
        # Clear existing index
        self.conn.execute("DELETE FROM transcript_fts")
        self.conn.commit()

        episodes_indexed = 0
        segments_indexed = 0

        for episode_id, transcript_path in episode_transcripts.items():
            count = self.index_episode(episode_id, transcript_path)
            if count > 0:
                episodes_indexed += 1
                segments_indexed += count

        return episodes_indexed, segments_indexed

    def unified_search(
        self,
        query: str,
        feed_id: Optional[int] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> UnifiedSearchResponse:
        """Unified search across episodes and transcripts.

        Searches both episode FTS (title/description) and transcript FTS,
        groups results by episode, and returns merged results.

        Args:
            query: Search query (supports FTS5 syntax).
            feed_id: Optional feed ID to filter results.
            limit: Maximum results to return.
            offset: Offset for pagination.

        Returns:
            UnifiedSearchResponse with grouped results and total count.
        """
        safe_query = query.strip()
        if not safe_query:
            return UnifiedSearchResponse(query=query, total=0, results=[])

        # Normalize query for FTS (match word boundaries, like existing episode search)
        fts_query = " ".join(word for word in safe_query.split() if word)

        # Build feed filter clauses
        episode_feed_filter = ""
        transcript_feed_filter = ""
        episode_feed_params: list = []
        transcript_feed_params: list = []
        if feed_id is not None:
            episode_feed_filter = " AND ef.feed_id = ?"
            transcript_feed_filter = " AND e.feed_id = ?"
            episode_feed_params = [feed_id]
            transcript_feed_params = [feed_id]

        # Query 1: Get episodes matching in episode_fts (title/description)
        # episode_fts has columns: title, description, episode_id, feed_id
        episode_fts_sql = f"""
            SELECT
                ef.episode_id,
                e.title as episode_title,
                e.feed_id,
                COALESCE(f.custom_title, f.title) as feed_title,
                e.published_at,
                bm25(episode_fts) as rank
            FROM episode_fts ef
            JOIN episode e ON ef.episode_id = e.id
            JOIN feed f ON e.feed_id = f.id
            WHERE episode_fts MATCH ?{episode_feed_filter}
        """

        # Query 2: Get episodes with transcript matches and count
        # Note: bm25() can't be used with aggregate functions, so we get best rank via subquery
        transcript_fts_sql = f"""
            SELECT
                e.id as episode_id,
                e.title as episode_title,
                e.feed_id,
                COALESCE(f.custom_title, f.title) as feed_title,
                e.published_at,
                (SELECT bm25(transcript_fts) FROM transcript_fts
                 WHERE text MATCH ? AND episode_id = e.id
                 ORDER BY bm25(transcript_fts) LIMIT 1) as rank,
                (SELECT COUNT(*) FROM transcript_fts
                 WHERE text MATCH ? AND episode_id = e.id) as match_count
            FROM episode e
            JOIN feed f ON e.feed_id = f.id
            WHERE e.id IN (
                SELECT DISTINCT episode_id FROM transcript_fts WHERE text MATCH ?
            ){transcript_feed_filter}
        """

        # Combine and aggregate results in Python
        try:
            # Get episode FTS matches
            episode_matches: dict[int, dict] = {}
            cursor = self.conn.execute(
                episode_fts_sql,
                [fts_query] + episode_feed_params,
            )
            for row in cursor.fetchall():
                ep_id = row[0]
                episode_matches[ep_id] = {
                    "episode_id": ep_id,
                    "episode_title": row[1],
                    "feed_id": row[2],
                    "feed_title": row[3],
                    "published_at": row[4],
                    "match_sources": ["episode"],  # Matches in title or description
                    "transcript_match_count": 0,
                    "best_rank": row[5],
                }

            # Get transcript FTS matches
            cursor = self.conn.execute(
                transcript_fts_sql,
                [fts_query, fts_query, fts_query] + transcript_feed_params,
            )
            for row in cursor.fetchall():
                ep_id = row[0]
                if ep_id in episode_matches:
                    # Merge with existing episode match
                    episode_matches[ep_id]["match_sources"].append("transcript")
                    episode_matches[ep_id]["transcript_match_count"] = row[6]
                    # Keep the better (lower) rank
                    if row[5] < episode_matches[ep_id]["best_rank"]:
                        episode_matches[ep_id]["best_rank"] = row[5]
                else:
                    # New episode from transcript match
                    episode_matches[ep_id] = {
                        "episode_id": ep_id,
                        "episode_title": row[1],
                        "feed_id": row[2],
                        "feed_title": row[3],
                        "published_at": row[4],
                        "match_sources": ["transcript"],
                        "transcript_match_count": row[6],
                        "best_rank": row[5],
                    }

        except sqlite3.OperationalError:
            # Invalid FTS5 query syntax
            return UnifiedSearchResponse(query=query, total=0, results=[])

        # Sort by rank (ascending, since BM25 returns negative values where lower is better)
        # Then by published_at (descending for recency)
        def sort_key(x):
            rank = x["best_rank"]
            # For secondary sort by date (descending), we reverse the string
            # since we can't negate a string
            pub = x["published_at"] or ""
            return (rank, pub)

        # Sort by rank ascending, then by date descending (reverse=True for date would mess up rank)
        # So we do two-phase sort: first by date descending, then stable sort by rank ascending
        sorted_results = sorted(episode_matches.values(), key=lambda x: x["published_at"] or "", reverse=True)
        sorted_results = sorted(sorted_results, key=lambda x: x["best_rank"])

        total = len(sorted_results)

        # Apply pagination
        paginated = sorted_results[offset : offset + limit]

        results = [
            UnifiedSearchResult(
                episode_id=r["episode_id"],
                episode_title=r["episode_title"],
                feed_id=r["feed_id"],
                feed_title=r["feed_title"],
                published_at=r["published_at"],
                match_sources=r["match_sources"],
                transcript_match_count=r["transcript_match_count"],
                best_rank=r["best_rank"],
            )
            for r in paginated
        ]

        return UnifiedSearchResponse(query=query, total=total, results=results)

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

        # Parse transcript
        segments = parse_transcript_file(path)
        if not segments:
            return 0

        # Get feed_id for the episode
        episode_repo = EpisodeRepository(self.conn)
        episode = episode_repo.get_by_id(episode_id)
        if not episode:
            return 0
        feed_id = episode.feed_id

        # Remove existing embeddings for this episode
        self.conn.execute(
            "DELETE FROM segment_vec WHERE episode_id = ?",
            (episode_id,),
        )

        # Generate embeddings in batch
        texts = [seg.text for seg in segments]
        embeddings = generate_embeddings_batch(texts, model_name)

        # Insert embeddings into vec0 table
        # Column order: embedding, then auxiliary columns
        # Note: vec0 FLOAT columns require explicit float type (not int)
        for segment, embedding in zip(segments, embeddings):
            self.conn.execute(
                """
                INSERT INTO segment_vec
                (embedding, episode_id, feed_id, segment_start, segment_end, text_hash, model_name)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    embedding,
                    episode_id,
                    feed_id,
                    float(segment.start),
                    float(segment.end),
                    text_hash(segment.text),
                    model_name,
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
        cursor = self.conn.execute(
            "DELETE FROM segment_vec WHERE episode_id = ?",
            (episode_id,),
        )
        self.conn.commit()
        return cursor.rowcount

    def get_embedded_episodes(self) -> set[int]:
        """Get set of episode IDs that have embeddings."""
        try:
            cursor = self.conn.execute(
                "SELECT DISTINCT episode_id FROM segment_vec"
            )
            return {row[0] for row in cursor.fetchall()}
        except sqlite3.OperationalError:
            # Table doesn't exist (sqlite-vec not available)
            return set()

    def get_embedding_count(self) -> int:
        """Get total number of segment embeddings."""
        try:
            cursor = self.conn.execute("SELECT COUNT(*) FROM segment_vec")
            return cursor.fetchone()[0]
        except sqlite3.OperationalError:
            # Table doesn't exist (sqlite-vec not available)
            return 0

    def _vector_search(
        self,
        query_embedding: bytes,
        feed_id: int | None = None,
        limit: int = 50,
    ) -> list[tuple]:
        """Perform vector similarity search using vec0 indexed KNN.

        Args:
            query_embedding: Query embedding as bytes.
            feed_id: Optional feed ID to filter results.
            limit: Maximum results to return.

        Returns:
            List of tuples with segment info and distance.
        """
        from cast2md.db.connection import is_sqlite_vec_available

        if not is_sqlite_vec_available():
            return []

        # vec0 KNN search - uses indexed search for O(log n) performance
        # The MATCH syntax triggers the indexed KNN search
        # We fetch more results than needed if filtering by feed_id
        fetch_limit = limit * 3 if feed_id is not None else limit

        try:
            # First, get KNN results from vec0 using indexed search
            knn_sql = """
                SELECT
                    rowid,
                    episode_id,
                    feed_id,
                    segment_start,
                    segment_end,
                    distance
                FROM segment_vec
                WHERE embedding MATCH ?
                  AND k = ?
            """
            cursor = self.conn.execute(knn_sql, (query_embedding, fetch_limit))
            knn_results = cursor.fetchall()

            # Filter by feed_id if specified
            if feed_id is not None:
                knn_results = [r for r in knn_results if r[2] == feed_id][:limit]
            else:
                knn_results = knn_results[:limit]

            if not knn_results:
                return []

            # Get episode/feed metadata and text for the matched segments
            results = []
            for row in knn_results:
                _, ep_id, f_id, seg_start, seg_end, distance = row

                # Get episode and feed info
                meta_sql = """
                    SELECT
                        e.title as episode_title,
                        COALESCE(f.custom_title, f.title) as feed_title,
                        e.published_at
                    FROM episode e
                    JOIN feed f ON e.feed_id = f.id
                    WHERE e.id = ?
                """
                meta_cursor = self.conn.execute(meta_sql, (ep_id,))
                meta_row = meta_cursor.fetchone()
                if not meta_row:
                    continue

                # Get text from FTS table
                text_sql = """
                    SELECT text FROM transcript_fts
                    WHERE episode_id = ? AND segment_start = ? AND segment_end = ?
                """
                text_cursor = self.conn.execute(text_sql, (ep_id, seg_start, seg_end))
                text_row = text_cursor.fetchone()
                text = text_row[0] if text_row else ""

                results.append((
                    ep_id,
                    meta_row[0],  # episode_title
                    f_id,
                    meta_row[1],  # feed_title
                    meta_row[2],  # published_at
                    seg_start,
                    seg_end,
                    text,
                    distance,
                ))

            return results

        except sqlite3.OperationalError as e:
            logger.warning(f"Vector search failed: {e}")
            return []

    def hybrid_search(
        self,
        query: str,
        feed_id: int | None = None,
        limit: int = 20,
        mode: Literal["hybrid", "semantic", "keyword"] = "hybrid",
    ) -> HybridSearchResponse:
        """Perform hybrid search combining keyword and semantic search.

        Uses Reciprocal Rank Fusion (RRF) to combine results from FTS5
        keyword search and vector similarity search.

        Args:
            query: Search query.
            feed_id: Optional feed ID to filter results.
            limit: Maximum results to return.
            mode: Search mode - "hybrid", "semantic", or "keyword".

        Returns:
            HybridSearchResponse with combined results.
        """
        import time

        from cast2md.db.connection import is_sqlite_vec_available
        from cast2md.search.embeddings import generate_embedding, is_embeddings_available

        safe_query = query.strip()
        if not safe_query:
            return HybridSearchResponse(query=query, total=0, mode=mode, results=[])

        t_start = time.perf_counter()

        # RRF constant (standard value from literature)
        K = 60

        # Dictionary to collect results: key = (episode_id, segment_start, segment_end)
        results_map: dict[tuple, dict] = {}

        # Keyword search (FTS5)
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
            except sqlite3.OperationalError:
                # Invalid FTS5 query, continue with semantic only if in hybrid mode
                pass

        # Semantic search (vector similarity)
        if mode in ("hybrid", "semantic") and is_embeddings_available() and is_sqlite_vec_available():
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

        # Apply limit
        limited = sorted_results[:limit]

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
