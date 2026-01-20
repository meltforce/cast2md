"""Repository for transcript full-text search operations."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cast2md.search.parser import parse_transcript_file, TranscriptSegment


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
        sorted_results = sorted(
            episode_matches.values(),
            key=lambda x: (x["best_rank"], -(x["published_at"] or "") if x["published_at"] else ""),
        )

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
