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
