"""Episode discovery from RSS feeds."""

import logging
from dataclasses import dataclass

import httpx

from cast2md.config.settings import get_settings
from cast2md.db.connection import get_db
from cast2md.db.models import Feed, JobType
from cast2md.db.repository import EpisodeRepository, FeedRepository, JobRepository
from cast2md.feed.parser import ParsedFeed, parse_feed

logger = logging.getLogger(__name__)


@dataclass
class DiscoveryResult:
    """Result of episode discovery."""

    new_episode_ids: list[int]
    total_new: int


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
                "User-Agent": settings.user_agent
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
                "User-Agent": settings.user_agent
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


def discover_new_episodes(
    feed: Feed,
    auto_queue: bool = False,
    queue_only_latest: bool = False,
) -> DiscoveryResult:
    """Discover and store new episodes from a feed.

    Args:
        feed: Feed to poll for new episodes.
        auto_queue: Whether to automatically queue new episodes for processing.
        queue_only_latest: If True, only queue the most recent episode (for new feeds).

    Returns:
        DiscoveryResult with list of new episode IDs and count.
    """
    # Fetch and parse feed
    content = fetch_feed_sync(feed.url)
    parsed = parse_feed(content)

    new_episode_ids = []

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)
        job_repo = JobRepository(conn)

        for ep in parsed.episodes:
            # Skip if already exists
            if episode_repo.exists(feed.id, ep.guid):
                continue

            episode = episode_repo.create(
                feed_id=feed.id,
                guid=ep.guid,
                title=ep.title,
                audio_url=ep.audio_url,
                description=ep.description,
                duration_seconds=ep.duration_seconds,
                published_at=ep.published_at,
                transcript_url=ep.transcript_url,
            )
            new_episode_ids.append(episode.id)

        # Update last polled timestamp
        feed_repo.update_last_polled(feed.id)

        # Auto-queue if requested
        if auto_queue and new_episode_ids:
            episodes_to_queue = new_episode_ids[:1] if queue_only_latest else new_episode_ids

            for episode_id in episodes_to_queue:
                episode = episode_repo.get_by_id(episode_id)

                # Skip if already downloaded or has pending job
                if episode.audio_path:
                    logger.debug(f"Skipping {episode.title} - already downloaded")
                    continue
                if job_repo.has_pending_job(episode_id, JobType.DOWNLOAD):
                    continue

                # Queue download job with priority 1 (high) for new episodes
                job_repo.create(
                    episode_id=episode_id,
                    job_type=JobType.DOWNLOAD,
                    priority=1,
                )
                logger.info(f"Auto-queued episode for processing: {episode.title}")

    return DiscoveryResult(
        new_episode_ids=new_episode_ids,
        total_new=len(new_episode_ids),
    )
