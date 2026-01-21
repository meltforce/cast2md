"""iTunes search API endpoints."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from cast2md.clients.itunes import ItunesClient

router = APIRouter(prefix="/api/itunes", tags=["itunes"])


class ItunesSearchResult(BaseModel):
    """A single iTunes search result."""

    itunes_id: str
    title: str
    author: str
    feed_url: str
    artwork_url: str | None


class ItunesSearchResponse(BaseModel):
    """Response model for iTunes search."""

    query: str
    results: list[ItunesSearchResult]


@router.get("/search", response_model=ItunesSearchResponse)
def search_itunes(
    q: str = Query(..., min_length=1, description="Search term"),
    limit: int = Query(10, ge=1, le=25, description="Maximum results"),
) -> ItunesSearchResponse:
    """Search for podcasts in the iTunes catalog.

    Only returns podcasts that have RSS feed URLs.
    """
    client = ItunesClient()
    results = client.search(q, limit=limit)

    # Filter to only podcasts with RSS feed URLs
    filtered = [
        ItunesSearchResult(
            itunes_id=r.itunes_id,
            title=r.title,
            author=r.author,
            feed_url=r.feed_url,
            artwork_url=r.artwork_url,
        )
        for r in results
        if r.feed_url
    ]

    return ItunesSearchResponse(query=q, results=filtered)
