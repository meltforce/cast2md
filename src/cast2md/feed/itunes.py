"""iTunes URL resolution for podcast feeds.

Converts Apple Podcasts URLs to RSS feed URLs via the iTunes Lookup API.
"""

import logging
import re
from typing import Optional

from cast2md.clients.itunes import ItunesClient

logger = logging.getLogger(__name__)

# Patterns for Apple Podcasts URLs
# Examples:
#   - https://podcasts.apple.com/us/podcast/the-daily/id1200361736
#   - https://podcasts.apple.com/podcast/id1200361736
#   - podcasts.apple.com/us/podcast/some-podcast/id1234567890?i=1000123456789
ITUNES_URL_PATTERN = re.compile(r"podcasts\.apple\.com/.*/id(\d+)")


def extract_itunes_id(url: str) -> Optional[str]:
    """Extract iTunes ID from an Apple Podcasts URL.

    Args:
        url: Input URL (Apple Podcasts or other).

    Returns:
        iTunes ID string if found, None otherwise.
    """
    match = ITUNES_URL_PATTERN.search(url)
    return match.group(1) if match else None


def is_itunes_url(url: str) -> bool:
    """Check if URL is an Apple Podcasts URL.

    Args:
        url: URL to check.

    Returns:
        True if URL contains an iTunes podcast ID.
    """
    return extract_itunes_id(url) is not None


def resolve_feed_url(input_url: str) -> tuple[str, Optional[str]]:
    """Resolve input URL to RSS feed URL.

    If the input is an Apple Podcasts URL, calls the iTunes API to get the RSS
    feed URL. Otherwise, returns the input URL unchanged.

    Args:
        input_url: Either an Apple Podcasts URL or direct RSS URL.

    Returns:
        Tuple of (rss_url, itunes_id or None).

    Raises:
        ValueError: If iTunes lookup fails or podcast has no RSS feed URL.
    """
    itunes_id = extract_itunes_id(input_url)

    if itunes_id:
        logger.info(f"Detected Apple Podcasts URL with iTunes ID: {itunes_id}")

        client = ItunesClient()
        result = client.lookup(itunes_id)

        if not result:
            raise ValueError(f"Podcast not found for iTunes ID: {itunes_id}")

        if not result.feed_url:
            raise ValueError(
                f"No RSS feed URL available for podcast '{result.title}' (iTunes ID: {itunes_id}). "
                "The podcast may have been removed from Apple Podcasts."
            )

        logger.info(f"Resolved iTunes ID {itunes_id} to RSS feed: {result.feed_url}")
        return result.feed_url, itunes_id

    # Direct RSS URL - return unchanged
    return input_url, None
