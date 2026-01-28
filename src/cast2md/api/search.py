"""API endpoints for transcript search."""

from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from cast2md.db.connection import get_db
from cast2md.db.repository import EpisodeRepository, FeedRepository
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


class EpisodeResult(BaseModel):
    """An episode matching the search query."""

    id: int
    feed_id: int
    feed_title: str
    title: str
    description: str | None
    published_at: str | None
    status: str


class EpisodeSearchResponse(BaseModel):
    """Response from episode search."""

    query: str
    total: int
    results: list[EpisodeResult]


@router.get("/episodes", response_model=EpisodeSearchResponse)
def search_episodes(
    q: str = Query(..., min_length=1, description="Search query"),
    feed_id: int | None = Query(None, description="Filter by feed ID"),
    limit: int = Query(20, ge=1, le=100, description="Max results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """Search episodes by title and description using full-text search.

    Supports FTS5 query syntax:
    - Simple terms: `ai`
    - Phrases: `"machine learning"`
    - Boolean: `python AND async`, `docker OR kubernetes`
    - Negation: `python NOT flask`

    Returns matching episodes with feed info.
    """
    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)

        episodes, total = episode_repo.search_episodes_fts_full(
            query=q,
            feed_id=feed_id,
            limit=limit,
            offset=offset,
        )

        # Build results with feed titles
        results = []
        feed_cache: dict[int, str] = {}
        for ep in episodes:
            if ep.feed_id not in feed_cache:
                feed = feed_repo.get_by_id(ep.feed_id)
                feed_cache[ep.feed_id] = feed.title if feed else "Unknown"

            results.append(
                EpisodeResult(
                    id=ep.id,
                    feed_id=ep.feed_id,
                    feed_title=feed_cache[ep.feed_id],
                    title=ep.title,
                    description=ep.description,
                    published_at=ep.published_at.isoformat() if ep.published_at else None,
                    status=ep.status.value,
                )
            )

    return EpisodeSearchResponse(
        query=q,
        total=total,
        results=results,
    )


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


class TranscriptMatch(BaseModel):
    """A matching segment within a transcript."""

    segment_start: float
    segment_end: float
    snippet: str


class EpisodeDetailResponse(BaseModel):
    """Detailed episode info for search modal."""

    id: int
    title: str
    feed_id: int
    feed_title: str
    published_at: str | None
    duration: int | None
    description: str | None
    transcript_matches: list[TranscriptMatch]


@router.get("/episode-detail/{episode_id}", response_model=EpisodeDetailResponse)
def get_episode_detail(
    episode_id: int,
    q: str | None = Query(None, description="Search query for transcript matches"),
):
    """Get episode detail for search modal.

    Returns episode metadata, full description/show notes, and optionally
    transcript matches if a query is provided.
    """
    from fastapi import HTTPException

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)
        search_repo = TranscriptSearchRepository(conn)

        episode = episode_repo.get_by_id(episode_id)
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")

        feed = feed_repo.get_by_id(episode.feed_id)
        feed_title = feed.display_title if feed else "Unknown"

        # Get transcript matches if query provided
        transcript_matches = []
        if q:
            results = search_repo.search_episode(
                episode_id=episode_id,
                query=q,
                limit=50,
            )
            transcript_matches = [
                TranscriptMatch(
                    segment_start=r.segment_start,
                    segment_end=r.segment_end,
                    snippet=r.snippet,
                )
                for r in results
            ]

    return EpisodeDetailResponse(
        id=episode.id,
        title=episode.title,
        feed_id=episode.feed_id,
        feed_title=feed_title,
        published_at=episode.published_at.isoformat() if episode.published_at else None,
        duration=episode.duration_seconds,
        description=episode.description,
        transcript_matches=transcript_matches,
    )


class SemanticResult(BaseModel):
    """A result from semantic/hybrid search."""

    episode_id: int
    episode_title: str
    feed_id: int
    feed_title: str
    published_at: str | None
    segment_start: float
    segment_end: float
    text: str
    score: float
    match_type: str
    result_type: str = "transcript"  # "episode" or "transcript"


class SemanticSearchResponse(BaseModel):
    """Response from semantic search."""

    query: str
    total: int
    mode: str
    results: list[SemanticResult]


class SemanticSearchStats(BaseModel):
    """Statistics about semantic search index."""

    total_embeddings: int
    embedded_episodes: int
    embeddings_available: bool


@router.get("/semantic", response_model=SemanticSearchResponse)
def semantic_search(
    q: str = Query(..., min_length=1, description="Search query"),
    feed_id: int | None = Query(None, description="Filter by feed ID"),
    limit: int = Query(20, ge=1, le=100, description="Max results"),
    mode: Literal["hybrid", "semantic", "keyword"] = Query(
        "hybrid", description="Search mode: hybrid, semantic, or keyword"
    ),
):
    """Search transcripts using natural language understanding.

    Combines keyword search (FTS5) with semantic search (embeddings)
    using Reciprocal Rank Fusion for best results.

    Modes:
    - **hybrid**: Combines keyword + semantic results (recommended)
    - **semantic**: Only semantic/vector similarity search
    - **keyword**: Only FTS5 keyword search

    Example queries:
    - "protein and strength" - finds content about muscle, fitness, nutrition
    - "machine learning" - exact keyword match
    - "discussions about building muscle" - conceptual search
    """
    with get_db() as conn:
        search_repo = TranscriptSearchRepository(conn)
        response = search_repo.hybrid_search(
            query=q,
            feed_id=feed_id,
            limit=limit,
            mode=mode,
        )

    return SemanticSearchResponse(
        query=response.query,
        total=response.total,
        mode=response.mode,
        results=[
            SemanticResult(
                episode_id=r.episode_id,
                episode_title=r.episode_title,
                feed_id=r.feed_id,
                feed_title=r.feed_title,
                published_at=r.published_at.isoformat() if hasattr(r.published_at, 'isoformat') else r.published_at,
                segment_start=r.segment_start,
                segment_end=r.segment_end,
                text=r.text,
                score=r.score,
                match_type=r.match_type,
                result_type=r.result_type,
            )
            for r in response.results
        ],
    )


@router.get("/semantic/stats", response_model=SemanticSearchStats)
def get_semantic_stats():
    """Get statistics about the semantic search index."""
    from cast2md.search.embeddings import is_embeddings_available

    with get_db() as conn:
        search_repo = TranscriptSearchRepository(conn)
        total_embeddings = search_repo.get_embedding_count()
        embedded_episodes = len(search_repo.get_embedded_episodes())

    return SemanticSearchStats(
        total_embeddings=total_embeddings,
        embedded_episodes=embedded_episodes,
        embeddings_available=is_embeddings_available(),
    )
