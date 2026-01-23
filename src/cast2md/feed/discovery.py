"""Episode discovery from RSS feeds."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import httpx

from cast2md.config.settings import get_settings
from cast2md.db.connection import get_db
from cast2md.db.models import Episode, EpisodeStatus, Feed, JobType
from cast2md.db.repository import EpisodeRepository, FeedRepository, JobRepository
from cast2md.feed.parser import ParsedFeed, parse_feed

logger = logging.getLogger(__name__)


def _normalize_title(title: str) -> str:
    """Normalize title for comparison."""
    title = re.sub(
        r"^(#\d+[:\s\-]+|Ep\.?\s*\d+\s*[:\-]+|Episode\s+\d+[:\s\-]+)",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"[^\w\s]", "", title)
    return " ".join(title.lower().split())


def _titles_similar(title1: str, title2: str) -> bool:
    """Check if two titles are similar enough to be the same episode."""
    norm1 = _normalize_title(title1)
    norm2 = _normalize_title(title2)
    if norm1 == norm2:
        return True
    if len(norm1) >= 10 and len(norm2) >= 10:
        if norm1 in norm2 or norm2 in norm1:
            return True
    return False


def _authors_match(author1: Optional[str], author2: Optional[str]) -> bool:
    """Check if two author strings match."""
    if not author1 or not author2:
        return False
    a1 = author1.lower().strip()
    a2 = author2.lower().strip()
    if a1 == a2:
        return True
    if a1 in a2 or a2 in a1:
        return True
    return False


def _parse_published_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse a date string to datetime."""
    if not date_str:
        return None
    formats = ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]
    for fmt in formats:
        try:
            return datetime.strptime(date_str[: len("2024-01-01T00:00:00")], fmt)
        except (ValueError, TypeError):
            continue
    return None


def _dates_within_24h(date1: Optional[datetime], date2_str: Optional[str]) -> bool:
    """Check if two dates are within 24 hours of each other."""
    if not date1:
        return True
    date2 = _parse_published_date(date2_str)
    if not date2:
        return True
    return abs((date1 - date2).total_seconds()) < 86400


def _discover_pocketcasts_transcripts(
    feed: Feed,
    episodes: list[Episode],
    episode_repo: EpisodeRepository,
    feed_repo: FeedRepository,
) -> int:
    """Check Pocket Casts for transcripts of episodes without Podcast 2.0 tags.

    This is called AFTER episodes are created, only for episodes that don't have
    transcript_url (Podcast 2.0 tags). Pocket Casts is a fallback provider.

    Args:
        feed: The feed being discovered.
        episodes: List of newly created episodes.
        episode_repo: Episode repository.
        feed_repo: Feed repository.

    Returns:
        Number of episodes that have Pocket Casts transcripts available.
    """
    # Filter to episodes without Podcast 2.0 transcript URLs
    episodes_needing_check = [ep for ep in episodes if not ep.transcript_url]

    if not episodes_needing_check:
        logger.debug(f"All episodes have Podcast 2.0 transcripts, skipping Pocket Casts check")
        return 0

    # Lazy import to avoid circular dependencies
    from cast2md.clients.pocketcasts import PocketCastsClient

    client = PocketCastsClient()

    # Get or search for Pocket Casts show UUID
    show_uuid = feed.pocketcasts_uuid
    if not show_uuid:
        # Search by feed title
        results = client.search(feed.title)
        if not results:
            logger.debug(f"Podcast not found on Pocket Casts: {feed.title}")
            return 0

        # Match by author
        for show in results:
            if _authors_match(show.author, feed.author):
                show_uuid = show.uuid
                feed_repo.update_pocketcasts_uuid(feed.id, show_uuid)
                logger.info(f"Found Pocket Casts show UUID for '{feed.title}': {show_uuid}")
                break

        # If no author match, try exact title match
        if not show_uuid:
            for show in results:
                if show.title.lower().strip() == feed.title.lower().strip():
                    show_uuid = show.uuid
                    feed_repo.update_pocketcasts_uuid(feed.id, show_uuid)
                    logger.info(f"Found Pocket Casts show UUID for '{feed.title}' (title match): {show_uuid}")
                    break

        if not show_uuid:
            logger.debug(f"Could not match Pocket Casts show for feed: {feed.title}")
            return 0

    # Get episodes from Pocket Casts
    pc_episodes = client.get_episodes(show_uuid)
    if not pc_episodes:
        logger.debug(f"No episodes found on Pocket Casts for show: {show_uuid}")
        return 0

    # Match our episodes with Pocket Casts episodes
    matched_count = 0
    for episode in episodes_needing_check:
        for pc_ep in pc_episodes:
            if _titles_similar(episode.title, pc_ep.title):
                if _dates_within_24h(episode.published_at, pc_ep.published):
                    if pc_ep.transcript_url:
                        episode_repo.update_pocketcasts_transcript_url(
                            episode.id, pc_ep.transcript_url
                        )
                        matched_count += 1
                        logger.debug(f"Found Pocket Casts transcript for: {episode.title}")
                    break

    if matched_count > 0:
        logger.info(f"Found {matched_count} Pocket Casts transcripts for feed: {feed.title}")

    return matched_count


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
    new_episodes = []

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)
        job_repo = JobRepository(conn)

        # Update feed metadata on every poll
        categories_json = json.dumps(parsed.categories) if parsed.categories else None
        feed_repo.update_metadata(
            feed_id=feed.id,
            author=parsed.author,
            link=parsed.link,
            categories=categories_json,
        )

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
                transcript_type=ep.transcript_type,
                link=ep.link,
                author=ep.author,
            )
            new_episode_ids.append(episode.id)
            new_episodes.append(episode)

        # Update last polled timestamp
        feed_repo.update_last_polled(feed.id)

        # Upfront Pocket Casts check: for episodes without Podcast 2.0 tags,
        # check if Pocket Casts has transcripts available
        if new_episodes:
            try:
                _discover_pocketcasts_transcripts(
                    feed=feed,
                    episodes=new_episodes,
                    episode_repo=episode_repo,
                    feed_repo=feed_repo,
                )
            except Exception as e:
                logger.warning(f"Error checking Pocket Casts for {feed.title}: {e}")

        # Auto-queue if requested
        # Queue transcript download jobs first (fast, tries external providers)
        # Episodes that get transcripts won't need audio download
        if auto_queue and new_episode_ids:
            episodes_to_queue = new_episode_ids[:1] if queue_only_latest else new_episode_ids
            settings = get_settings()
            unavailable_age = timedelta(days=settings.transcript_unavailable_age_days)
            now = datetime.now()

            for episode_id in episodes_to_queue:
                episode = episode_repo.get_by_id(episode_id)

                # Skip if already has transcript or has pending job
                if episode.transcript_path:
                    logger.debug(f"Skipping {episode.title} - already has transcript")
                    continue
                if job_repo.has_pending_job(episode_id, JobType.TRANSCRIPT_DOWNLOAD):
                    continue
                if job_repo.has_pending_job(episode_id, JobType.DOWNLOAD):
                    continue

                # Check if episode is old and has no external transcript URL
                has_external_url = episode.transcript_url or episode.pocketcasts_transcript_url
                episode_age = timedelta(days=365)  # Default to old if no published date
                if episode.published_at:
                    episode_age = now - episode.published_at

                if not has_external_url and episode_age >= unavailable_age:
                    # Old episode with no external URL - mark as needs_audio, skip queuing
                    episode_repo.update_transcript_check(
                        episode.id,
                        status=EpisodeStatus.NEEDS_AUDIO,
                        checked_at=now,
                        next_retry_at=None,
                        failure_reason="no_external_url_old_episode",
                    )
                    logger.debug(
                        f"Marked old episode as needs_audio: {episode.title} "
                        f"(age: {episode_age.days}d, threshold: {unavailable_age.days}d)"
                    )
                    continue

                # Queue transcript download job with priority 1 (high) for new episodes
                # This tries external providers first (Podcast 2.0, Pocket Casts)
                # If no transcript available, episode stays NEW for manual download
                job_repo.create(
                    episode_id=episode_id,
                    job_type=JobType.TRANSCRIPT_DOWNLOAD,
                    priority=1,
                )
                logger.info(f"Auto-queued transcript download for episode: {episode.title}")

    return DiscoveryResult(
        new_episode_ids=new_episode_ids,
        total_new=len(new_episode_ids),
    )
