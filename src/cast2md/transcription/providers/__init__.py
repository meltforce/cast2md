"""Transcript provider registry.

Provides a pluggable system for fetching transcripts from external sources
before falling back to Whisper transcription.
"""

import logging

from cast2md.db.models import Episode, Feed
from cast2md.transcription.providers.base import TranscriptError, TranscriptProvider, TranscriptResult
from cast2md.transcription.providers.pocketcasts import PocketCastsProvider
from cast2md.transcription.providers.podcast20 import Podcast20Provider

logger = logging.getLogger(__name__)

# Register providers in priority order
# Higher priority providers (like Podcast 2.0 which is free and authoritative)
# should come first. Pocket Casts is a fallback for podcasts without RSS transcripts.
_providers: list[TranscriptProvider] = [
    Podcast20Provider(),  # Priority 1: Publisher transcripts from RSS
    PocketCastsProvider(),  # Priority 2: Pocket Casts auto-generated
]


def try_fetch_transcript(episode: Episode, feed: Feed) -> TranscriptResult | TranscriptError | None:
    """Try all transcript providers in order, return first success or error.

    Providers are tried in priority order. The first provider that both
    can_provide() returns True AND fetch() returns a result wins.

    Args:
        episode: Episode to fetch transcript for.
        feed: Feed the episode belongs to.

    Returns:
        TranscriptResult from the first successful provider,
        TranscriptError if a provider returned an error (e.g., 403 forbidden),
        or None if no providers could provide a transcript.
    """
    last_error: TranscriptError | None = None

    for provider in _providers:
        if not provider.can_provide(episode, feed):
            continue

        logger.debug(f"Trying provider {provider.source_id} for episode: {episode.title}")

        try:
            result = provider.fetch(episode, feed)

            if isinstance(result, TranscriptResult):
                logger.info(
                    f"Provider {provider.source_id} succeeded for episode: {episode.title}"
                )
                return result
            elif isinstance(result, TranscriptError):
                # Track error but continue to try other providers
                logger.debug(
                    f"Provider {provider.source_id} returned error {result.error_type} "
                    f"for episode: {episode.title}"
                )
                last_error = result
                continue
            # result is None - continue to next provider

        except Exception as e:
            logger.warning(
                f"Provider {provider.source_id} failed for episode {episode.title}: {e}"
            )
            continue

    # If we got an error from any provider, return it (useful for 403 handling)
    if last_error:
        logger.debug(f"Returning last error for episode: {episode.title} - {last_error.error_type}")
        return last_error

    logger.debug(f"No providers could fetch transcript for episode: {episode.title}")
    return None


def get_available_providers() -> list[str]:
    """Get list of registered provider IDs."""
    return [p.source_id for p in _providers]


__all__ = [
    "TranscriptProvider",
    "TranscriptResult",
    "TranscriptError",
    "try_fetch_transcript",
    "get_available_providers",
]
