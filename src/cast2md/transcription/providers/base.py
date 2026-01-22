"""Base class for transcript providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from cast2md.db.models import Episode, Feed


@dataclass
class TranscriptResult:
    """Result from a transcript provider.

    Attributes:
        content: Markdown content of the transcript.
        source: Source identifier (e.g., 'podcast2.0:vtt', 'podcast2.0:srt').
        source_url: Original URL the transcript was fetched from.
    """

    content: str
    source: str
    source_url: str


@dataclass
class TranscriptError:
    """Error from a failed transcript fetch attempt.

    Used to distinguish between "no transcript available" (None) and
    "transcript exists but we couldn't get it" (e.g., 403 forbidden).

    Attributes:
        error_type: Type of error ('forbidden', 'not_found', 'timeout', 'request_error').
        source: Provider identifier (e.g., 'podcast2.0', 'pocketcasts').
        source_url: URL that was attempted.
        status_code: HTTP status code if applicable.
    """

    error_type: str
    source: str
    source_url: Optional[str] = None
    status_code: Optional[int] = None


class TranscriptProvider(ABC):
    """Base class for transcript sources.

    Transcript providers fetch transcripts from external sources and convert
    them to markdown format. Providers are tried in priority order until one
    succeeds or all fail.
    """

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Return the provider identifier (e.g., 'podcast2.0', 'pocketcasts')."""
        ...

    @abstractmethod
    def can_provide(self, episode: Episode, feed: Feed) -> bool:
        """Check if this provider can get a transcript for this episode.

        Args:
            episode: Episode to check.
            feed: Feed the episode belongs to.

        Returns:
            True if this provider can potentially provide a transcript.
        """
        ...

    @abstractmethod
    def fetch(self, episode: Episode, feed: Feed) -> TranscriptResult | TranscriptError | None:
        """Fetch and convert transcript to markdown.

        Args:
            episode: Episode to fetch transcript for.
            feed: Feed the episode belongs to.

        Returns:
            TranscriptResult with markdown content and source info,
            TranscriptError if fetch failed with known error (e.g., 403),
            or None if provider cannot provide transcript.
        """
        ...
