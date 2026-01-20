"""API endpoints for transcript search."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from cast2md.db.connection import get_db
from cast2md.search.repository import TranscriptSearchRepository

router = APIRouter(prefix="/api/search", tags=["search"])


class SegmentResult(BaseModel):
    """A matching segment within a transcript."""

    episode_id: int
    episode_title: str
    feed_id: int
    feed_title: str
    published_at: str | None
    segment_start: float
    segment_end: float
    snippet: str
    rank: float


class SearchResponse(BaseModel):
    """Response from transcript search."""

    query: str
    total: int
    results: list[SegmentResult]


class IndexStats(BaseModel):
    """Statistics about the transcript search index."""

    total_segments: int
    indexed_episodes: int


@router.get("/transcripts", response_model=SearchResponse)
def search_transcripts(
    q: str = Query(..., min_length=1, description="Search query"),
    feed_id: int | None = Query(None, description="Filter by feed ID"),
    limit: int = Query(20, ge=1, le=100, description="Max results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """Search across all transcripts using full-text search.

    Supports FTS5 query syntax:
    - Simple terms: `kubernetes`
    - Phrases: `"machine learning"`
    - Boolean: `python AND async`, `docker OR kubernetes`
    - Negation: `python NOT flask`

    Returns matching segments with snippets and timestamps.
    """
    with get_db() as conn:
        search_repo = TranscriptSearchRepository(conn)
        response = search_repo.search(
            query=q,
            feed_id=feed_id,
            limit=limit,
            offset=offset,
        )

    return SearchResponse(
        query=response.query,
        total=response.total,
        results=[
            SegmentResult(
                episode_id=r.episode_id,
                episode_title=r.episode_title,
                feed_id=r.feed_id,
                feed_title=r.feed_title,
                published_at=r.published_at,
                segment_start=r.segment_start,
                segment_end=r.segment_end,
                snippet=r.snippet,
                rank=r.rank,
            )
            for r in response.results
        ],
    )


@router.get("/transcripts/episode/{episode_id}", response_model=list[SegmentResult])
def search_episode_transcript(
    episode_id: int,
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(50, ge=1, le=200, description="Max results"),
):
    """Search within a specific episode's transcript.

    Returns matching segments ordered by timestamp.
    """
    with get_db() as conn:
        search_repo = TranscriptSearchRepository(conn)
        results = search_repo.search_episode(
            episode_id=episode_id,
            query=q,
            limit=limit,
        )

    return [
        SegmentResult(
            episode_id=r.episode_id,
            episode_title=r.episode_title,
            feed_id=r.feed_id,
            feed_title=r.feed_title,
            published_at=r.published_at,
            segment_start=r.segment_start,
            segment_end=r.segment_end,
            snippet=r.snippet,
            rank=r.rank,
        )
        for r in results
    ]


@router.get("/stats", response_model=IndexStats)
def get_search_stats():
    """Get statistics about the transcript search index."""
    with get_db() as conn:
        search_repo = TranscriptSearchRepository(conn)
        total_segments = search_repo.get_indexed_count()
        indexed_episodes = len(search_repo.get_indexed_episodes())

    return IndexStats(
        total_segments=total_segments,
        indexed_episodes=indexed_episodes,
    )


@router.post("/reindex/{episode_id}")
def reindex_episode(episode_id: int):
    """Reindex a specific episode's transcript."""
    from cast2md.db.repository import EpisodeRepository

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        search_repo = TranscriptSearchRepository(conn)

        episode = episode_repo.get_by_id(episode_id)
        if not episode:
            return {"error": "Episode not found"}

        if not episode.transcript_path:
            return {"error": "Episode has no transcript"}

        count = search_repo.index_episode(episode_id, episode.transcript_path)

    return {
        "message": f"Indexed {count} segments for episode {episode_id}",
        "segments": count,
    }
