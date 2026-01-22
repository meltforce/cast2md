"""Pocket Casts transcript provider.

Fetches auto-generated transcripts from Pocket Casts public API.
This is a fallback provider used when RSS feeds don't include transcripts.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from cast2md.clients.pocketcasts import PocketCastsClient, PocketCastsEpisode
from cast2md.db.connection import get_db
from cast2md.db.models import Episode, Feed
from cast2md.db.repository import FeedRepository
from cast2md.transcription.formats import convert_to_markdown
from cast2md.transcription.providers.base import TranscriptError, TranscriptProvider, TranscriptResult

logger = logging.getLogger(__name__)


def _normalize_title(title: str) -> str:
    """Normalize title for comparison.

    Removes common prefixes like episode numbers, special characters,
    and normalizes whitespace for fuzzy matching.
    """
    # Remove episode number prefixes (e.g., "#123:", "Ep. 45 -", "Episode 123:")
    title = re.sub(r"^(#\d+[:\s\-]+|Ep\.?\s*\d+\s*[:\-]+|Episode\s+\d+[:\s\-]+)", "", title, flags=re.IGNORECASE)
    # Remove special characters
    title = re.sub(r"[^\w\s]", "", title)
    # Normalize whitespace and lowercase
    title = " ".join(title.lower().split())
    return title


def _titles_similar(title1: str, title2: str) -> bool:
    """Check if two titles are similar enough to be the same episode."""
    norm1 = _normalize_title(title1)
    norm2 = _normalize_title(title2)

    # Direct match
    if norm1 == norm2:
        return True

    # One is contained in the other (handles truncation)
    if len(norm1) >= 10 and len(norm2) >= 10:
        if norm1 in norm2 or norm2 in norm1:
            return True

    return False


def _authors_match(author1: str, author2: str) -> bool:
    """Check if two author strings match.

    Handles variations like "John Doe" vs "John Doe, MD" or "John Doe Productions".
    """
    if not author1 or not author2:
        return False

    # Normalize: lowercase, strip whitespace
    a1 = author1.lower().strip()
    a2 = author2.lower().strip()

    # Direct match
    if a1 == a2:
        return True

    # One contains the other
    if a1 in a2 or a2 in a1:
        return True

    return False


def _parse_published_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse a date string to datetime, handling various formats."""
    if not date_str:
        return None

    # Try common formats
    formats = [
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str[:len("2024-01-01T00:00:00")], fmt[:len(fmt)])
        except (ValueError, TypeError):
            continue

    return None


def _dates_within_24h(date1: Optional[datetime], date2_str: Optional[str]) -> bool:
    """Check if two dates are within 24 hours of each other."""
    if not date1:
        return True  # If we don't have our date, don't filter by date

    date2 = _parse_published_date(date2_str)
    if not date2:
        return True  # If we can't parse their date, don't filter

    return abs((date1 - date2).total_seconds()) < 86400  # 24 hours


class PocketCastsProvider(TranscriptProvider):
    """Transcript provider using Pocket Casts public API.

    Pocket Casts auto-generates transcripts for many podcasts. This provider
    searches for the podcast, matches episodes by title and date, and downloads
    the VTT transcript.

    The provider caches the Pocket Casts show UUID on the Feed model to avoid
    repeated searches.
    """

    @property
    def source_id(self) -> str:
        return "pocketcasts"

    def can_provide(self, episode: Episode, feed: Feed) -> bool:
        """Always returns True - Pocket Casts is a fallback provider.

        The API is public and we can search for any podcast.
        Actual availability is determined in fetch().
        """
        return True

    def fetch(self, episode: Episode, feed: Feed) -> TranscriptResult | TranscriptError | None:
        """Fetch transcript from Pocket Casts.

        1. Get or search for the Pocket Casts show UUID
        2. Get episodes from the show_notes API
        3. Match our episode by title + published date
        4. Download and convert the VTT transcript

        Args:
            episode: Episode to fetch transcript for.
            feed: Feed the episode belongs to.

        Returns:
            TranscriptResult with markdown content,
            TranscriptError if download failed (e.g., 403),
            or None if not available.
        """
        client = PocketCastsClient()

        # Get or search for Pocket Casts show UUID
        show_uuid = self._get_show_uuid(feed, client)
        if not show_uuid:
            logger.debug(f"Could not find Pocket Casts show for feed: {feed.title}")
            return None

        # Get episodes from Pocket Casts
        pc_episodes = client.get_episodes(show_uuid)
        if not pc_episodes:
            logger.debug(f"No episodes found in Pocket Casts for show: {show_uuid}")
            return None

        # Match our episode
        pc_episode = self._match_episode(episode, pc_episodes)
        if not pc_episode:
            logger.debug(f"Could not match episode in Pocket Casts: {episode.title}")
            return None

        # Check if transcript is available
        if not pc_episode.transcript_url:
            logger.debug(f"No Pocket Casts transcript for episode: {episode.title}")
            return None

        # Download transcript
        result = client.download_transcript(pc_episode.transcript_url)
        if not result.success:
            # Return TranscriptError for known failures (especially 403)
            error_type = "forbidden" if result.status_code == 403 else (
                "not_found" if result.status_code == 404 else "request_error"
            )
            logger.debug(f"Pocket Casts transcript download failed for {episode.title}: {result.error}")
            return TranscriptError(
                error_type=error_type,
                source=self.source_id,
                source_url=pc_episode.transcript_url,
                status_code=result.status_code,
            )

        # Convert VTT to markdown
        try:
            markdown, format_id = convert_to_markdown(
                content=result.content,
                mime_type="text/vtt",
                title=episode.title,
                url=pc_episode.transcript_url,
            )

            logger.info(f"Successfully fetched Pocket Casts transcript for episode: {episode.title}")

            return TranscriptResult(
                content=markdown,
                source=self.source_id,
                source_url=pc_episode.transcript_url,
            )

        except Exception as e:
            logger.warning(f"Error converting Pocket Casts transcript: {e}")
            return None

    def _get_show_uuid(self, feed: Feed, client: PocketCastsClient) -> Optional[str]:
        """Get Pocket Casts show UUID, searching if not cached.

        If the feed already has a pocketcasts_uuid, returns it.
        Otherwise, searches Pocket Casts by feed title and matches by author.
        If found, caches the UUID on the feed for future use.
        """
        # Use cached UUID if available
        if feed.pocketcasts_uuid:
            return feed.pocketcasts_uuid

        # Search by feed title
        results = client.search(feed.title)
        if not results:
            return None

        # Match by author
        for show in results:
            if _authors_match(show.author, feed.author):
                # Cache the UUID for future use
                self._cache_uuid(feed.id, show.uuid)
                logger.info(f"Found Pocket Casts show UUID for '{feed.title}': {show.uuid}")
                return show.uuid

        # If no author match, take first result if title is exact match
        for show in results:
            if show.title.lower().strip() == feed.title.lower().strip():
                self._cache_uuid(feed.id, show.uuid)
                logger.info(f"Found Pocket Casts show UUID for '{feed.title}' (title match): {show.uuid}")
                return show.uuid

        return None

    def _cache_uuid(self, feed_id: int, uuid: str) -> None:
        """Cache Pocket Casts UUID on the feed."""
        try:
            with get_db() as conn:
                repo = FeedRepository(conn)
                repo.update_pocketcasts_uuid(feed_id, uuid)
        except Exception as e:
            logger.warning(f"Failed to cache Pocket Casts UUID for feed {feed_id}: {e}")

    def _match_episode(
        self, episode: Episode, pc_episodes: list[PocketCastsEpisode]
    ) -> Optional[PocketCastsEpisode]:
        """Match our episode against Pocket Casts episodes.

        Matches by title similarity and published date within 24 hours.
        """
        for pc_ep in pc_episodes:
            if _titles_similar(episode.title, pc_ep.title):
                if _dates_within_24h(episode.published_at, pc_ep.published):
                    return pc_ep

        return None
