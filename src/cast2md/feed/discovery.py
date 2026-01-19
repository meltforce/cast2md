"""Episode discovery from RSS feeds."""

import httpx

from cast2md.config.settings import get_settings
from cast2md.db.connection import get_db
from cast2md.db.models import Feed
from cast2md.db.repository import EpisodeRepository, FeedRepository
from cast2md.feed.parser import ParsedFeed, parse_feed


async def fetch_feed(url: str) -> str:
    """Fetch RSS feed content from URL.

    Args:
        url: Feed URL.

    Returns:
        Raw feed content.

    Raises:
        httpx.HTTPError: If request fails.
    """
    settings = get_settings()

    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        response = await client.get(
            url,
            follow_redirects=True,
            headers={
                "User-Agent": "cast2md/0.1.0 (Podcast Transcription Service)"
            },
        )
        response.raise_for_status()
        return response.text


def fetch_feed_sync(url: str) -> str:
    """Synchronously fetch RSS feed content from URL.

    Args:
        url: Feed URL.

    Returns:
        Raw feed content.

    Raises:
        httpx.HTTPError: If request fails.
    """
    settings = get_settings()

    with httpx.Client(timeout=settings.request_timeout) as client:
        response = client.get(
            url,
            follow_redirects=True,
            headers={
                "User-Agent": "cast2md/0.1.0 (Podcast Transcription Service)"
            },
        )
        response.raise_for_status()
        return response.text


def validate_feed_url(url: str) -> tuple[bool, str, ParsedFeed | None]:
    """Validate a feed URL and return parsed feed data.

    Args:
        url: URL to validate.

    Returns:
        Tuple of (is_valid, message, parsed_feed).
    """
    try:
        content = fetch_feed_sync(url)
    except httpx.HTTPError as e:
        return False, f"Failed to fetch feed: {e}", None

    try:
        parsed = parse_feed(content)
    except ValueError as e:
        return False, f"Invalid RSS feed: {e}", None

    if not parsed.episodes:
        return False, "Feed has no audio episodes", None

    return True, f"Found {len(parsed.episodes)} episodes", parsed


def discover_new_episodes(feed: Feed) -> int:
    """Discover and store new episodes from a feed.

    Args:
        feed: Feed to poll for new episodes.

    Returns:
        Number of new episodes discovered.
    """
    # Fetch and parse feed
    content = fetch_feed_sync(feed.url)
    parsed = parse_feed(content)

    new_count = 0

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)

        for ep in parsed.episodes:
            # Skip if already exists
            if episode_repo.exists(feed.id, ep.guid):
                continue

            episode_repo.create(
                feed_id=feed.id,
                guid=ep.guid,
                title=ep.title,
                audio_url=ep.audio_url,
                description=ep.description,
                duration_seconds=ep.duration_seconds,
                published_at=ep.published_at,
                transcript_url=ep.transcript_url,
            )
            new_count += 1

        # Update last polled timestamp
        feed_repo.update_last_polled(feed.id)

    return new_count
