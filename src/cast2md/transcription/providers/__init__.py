"""Transcript provider registry.

Provides a pluggable system for fetching transcripts from external sources
before falling back to Whisper transcription.
"""

import logging
from typing import Optional

from cast2md.db.models import Episode, Feed
from cast2md.transcription.providers.base import TranscriptProvider, TranscriptResult
from cast2md.transcription.providers.podcast20 import Podcast20Provider

logger = logging.getLogger(__name__)

# Register providers in priority order
# Higher priority providers (like Podcast 2.0 which is free and authoritative)
# should come first. More complex providers (like Pocket Casts which requires
# authentication) can be added later.
_providers: list[TranscriptProvider] = [
    Podcast20Provider(),
    # Future: PocketCastsProvider(),
]


def try_fetch_transcript(episode: Episode, feed: Feed) -> Optional[TranscriptResult]:
    """Try all transcript providers in order, return first success.

    Providers are tried in priority order. The first provider that both
    can_provide() returns True AND fetch() returns a result wins.

    Args:
        episode: Episode to fetch transcript for.
        feed: Feed the episode belongs to.

    Returns:
        TranscriptResult from the first successful provider, or None if all fail.
    """
    for provider in _providers:
        if not provider.can_provide(episode, feed):
            continue

        logger.debug(f"Trying provider {provider.source_id} for episode: {episode.title}")

        try:
            result = provider.fetch(episode, feed)
            if result:
                logger.info(
                    f"Provider {provider.source_id} succeeded for episode: {episode.title}"
                )
                return result
        except Exception as e:
            logger.warning(
                f"Provider {provider.source_id} failed for episode {episode.title}: {e}"
            )
            continue

    logger.debug(f"No providers could fetch transcript for episode: {episode.title}")
    return None


def get_available_providers() -> list[str]:
    """Get list of registered provider IDs."""
    return [p.source_id for p in _providers]


__all__ = [
    "TranscriptProvider",
    "TranscriptResult",
    "try_fetch_transcript",
    "get_available_providers",
]
