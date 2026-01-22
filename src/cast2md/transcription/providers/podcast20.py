"""Podcast 2.0 transcript provider.

Downloads transcripts from URLs specified in the podcast:transcript RSS element.
Supports VTT, SRT, JSON, and plain text formats.
"""

import logging
from typing import Optional

import httpx

from cast2md.config.settings import get_settings
from cast2md.db.models import Episode, Feed
from cast2md.transcription.formats import convert_to_markdown, detect_format_from_url
from cast2md.transcription.providers.base import TranscriptError, TranscriptProvider, TranscriptResult

logger = logging.getLogger(__name__)


class Podcast20Provider(TranscriptProvider):
    """Transcript provider for Podcasting 2.0 <podcast:transcript> elements.

    This provider downloads transcripts from URLs specified in the RSS feed's
    podcast:transcript element. The episode must have a transcript_url field
    populated from the feed parser.
    """

    @property
    def source_id(self) -> str:
        return "podcast2.0"

    def can_provide(self, episode: Episode, feed: Feed) -> bool:
        """Check if episode has a transcript URL from RSS feed."""
        return bool(episode.transcript_url)

    def fetch(self, episode: Episode, feed: Feed) -> TranscriptResult | TranscriptError | None:
        """Download and convert transcript from podcast:transcript URL.

        Args:
            episode: Episode with transcript_url populated.
            feed: Feed the episode belongs to.

        Returns:
            TranscriptResult with markdown content,
            TranscriptError if download failed (e.g., 403),
            or None if no transcript URL available.
        """
        if not episode.transcript_url:
            return None

        url = episode.transcript_url
        logger.info(f"Fetching transcript from {url}")

        try:
            settings = get_settings()

            with httpx.Client(timeout=settings.request_timeout) as client:
                response = client.get(
                    url,
                    follow_redirects=True,
                    headers={"User-Agent": settings.user_agent},
                )
                response.raise_for_status()

            content = response.text
            if not content or not content.strip():
                logger.warning(f"Empty transcript from {url}")
                return None

            # Determine MIME type
            # Priority: stored from RSS > Content-Type header > URL extension
            mime_type = episode.transcript_type

            if not mime_type:
                content_type = response.headers.get("content-type", "")
                # Extract MIME type (ignore charset etc.)
                mime_type = content_type.split(";")[0].strip()

            if not mime_type:
                mime_type = detect_format_from_url(url)

            if not mime_type:
                # Default to plain text
                mime_type = "text/plain"
                logger.info(f"Could not determine format for {url}, treating as plain text")

            # Convert to markdown
            markdown, format_id = convert_to_markdown(
                content=content,
                mime_type=mime_type,
                title=episode.title,
                url=url,
            )

            source = f"{self.source_id}:{format_id}"
            logger.info(f"Successfully fetched transcript ({source}) for episode: {episode.title}")

            return TranscriptResult(
                content=markdown,
                source=source,
                source_url=url,
            )

        except httpx.HTTPStatusError as e:
            logger.warning(f"HTTP error fetching transcript from {url}: {e.response.status_code}")
            error_type = "forbidden" if e.response.status_code == 403 else (
                "not_found" if e.response.status_code == 404 else "request_error"
            )
            return TranscriptError(
                error_type=error_type,
                source=self.source_id,
                source_url=url,
                status_code=e.response.status_code,
            )
        except httpx.RequestError as e:
            logger.warning(f"Request error fetching transcript from {url}: {e}")
            return TranscriptError(
                error_type="request_error",
                source=self.source_id,
                source_url=url,
            )
        except Exception as e:
            logger.warning(f"Error processing transcript from {url}: {e}")
            return None
