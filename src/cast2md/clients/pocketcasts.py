"""Pocket Casts API client for podcast search and transcript retrieval."""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from cast2md.config.settings import get_settings

logger = logging.getLogger(__name__)

# Rate limiting for Pocket Casts API (unofficial, be conservative)
_rate_limit_lock = threading.Lock()
_last_api_call = 0.0
_MIN_API_INTERVAL = 0.5  # Minimum 500ms between API calls


@dataclass
class PocketCastsShow:
    """Podcast show metadata from Pocket Casts API."""

    uuid: str
    title: str
    author: str


@dataclass
class PocketCastsEpisode:
    """Episode metadata from Pocket Casts API."""

    uuid: str
    title: str
    published: Optional[str]  # ISO format date string
    transcript_url: Optional[str]  # From pocket_casts_transcripts[]


@dataclass
class TranscriptDownloadResult:
    """Result from transcript download attempt."""

    content: Optional[str] = None
    success: bool = False
    status_code: Optional[int] = None
    error: Optional[str] = None


class PocketCastsClient:
    """Client for Pocket Casts public API.

    Provides methods to search podcasts and retrieve episode transcripts.
    All endpoints are public and require no authentication.
    Includes rate limiting to avoid overloading the unofficial API.
    """

    BASE_URL = "https://podcast-api.pocketcasts.com"

    def __init__(self, timeout: Optional[float] = None):
        """Initialize client.

        Args:
            timeout: Request timeout in seconds. Defaults to settings value.
        """
        settings = get_settings()
        self.timeout = timeout or settings.request_timeout
        self.user_agent = settings.user_agent

    def _wait_for_rate_limit(self) -> None:
        """Wait if needed to respect rate limiting."""
        global _last_api_call
        with _rate_limit_lock:
            now = time.time()
            elapsed = now - _last_api_call
            if elapsed < _MIN_API_INTERVAL:
                time.sleep(_MIN_API_INTERVAL - elapsed)
            _last_api_call = time.time()

    def search(self, term: str) -> list[PocketCastsShow]:
        """Search podcasts by name.

        Args:
            term: Search term (podcast title).

        Returns:
            List of matching shows.
        """
        self._wait_for_rate_limit()
        url = f"{self.BASE_URL}/discover/search"

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    url,
                    json={"term": term},
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": self.user_agent,
                    },
                )
                response.raise_for_status()

            data = response.json()

            # Response is a list of shows
            results = []
            for show in data if isinstance(data, list) else []:
                uuid = show.get("uuid")
                if not uuid:
                    continue

                results.append(
                    PocketCastsShow(
                        uuid=uuid,
                        title=show.get("title", ""),
                        author=show.get("author", ""),
                    )
                )

            return results

        except httpx.HTTPStatusError as e:
            logger.warning(f"HTTP error searching Pocket Casts: {e.response.status_code}")
            return []
        except httpx.RequestError as e:
            logger.warning(f"Request error searching Pocket Casts: {e}")
            return []
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Error parsing Pocket Casts search response: {e}")
            return []

    def get_episodes(self, show_uuid: str) -> list[PocketCastsEpisode]:
        """Get episodes for a show with transcript URLs.

        Args:
            show_uuid: Pocket Casts show UUID.

        Returns:
            List of episodes with available transcript info.
        """
        self._wait_for_rate_limit()
        url = f"{self.BASE_URL}/mobile/show_notes/full/{show_uuid}"

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    url,
                    headers={"User-Agent": self.user_agent},
                    follow_redirects=True,  # API returns redirect to static JSON
                )
                response.raise_for_status()

            data = response.json()

            # Structure: {"podcast": {"uuid": "...", "episodes": [...]}}
            podcast = data.get("podcast", {})
            episodes_data = podcast.get("episodes", [])

            results = []
            for ep in episodes_data:
                uuid = ep.get("uuid")
                if not uuid:
                    continue

                # Get transcript URL from pocket_casts_transcripts array
                # These are auto-generated by Pocket Casts (VTT format)
                transcript_url = None
                pc_transcripts = ep.get("pocket_casts_transcripts", [])
                if pc_transcripts and len(pc_transcripts) > 0:
                    transcript_url = pc_transcripts[0].get("url")

                results.append(
                    PocketCastsEpisode(
                        uuid=uuid,
                        title=ep.get("title", ""),
                        published=ep.get("published"),
                        transcript_url=transcript_url,
                    )
                )

            return results

        except httpx.HTTPStatusError as e:
            logger.warning(f"HTTP error getting Pocket Casts episodes: {e.response.status_code}")
            return []
        except httpx.RequestError as e:
            logger.warning(f"Request error getting Pocket Casts episodes: {e}")
            return []
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Error parsing Pocket Casts episodes response: {e}")
            return []

    def download_transcript(self, url: str) -> TranscriptDownloadResult:
        """Download transcript content from URL.

        Args:
            url: Transcript URL (typically VTT format).

        Returns:
            TranscriptDownloadResult with content on success, or status_code/error on failure.
        """
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    url,
                    follow_redirects=True,
                    headers={"User-Agent": self.user_agent},
                )
                response.raise_for_status()

            content = response.text
            if not content or not content.strip():
                logger.warning(f"Empty transcript from {url}")
                return TranscriptDownloadResult(
                    success=False,
                    status_code=response.status_code,
                    error="empty_response",
                )

            return TranscriptDownloadResult(
                content=content,
                success=True,
                status_code=response.status_code,
            )

        except httpx.HTTPStatusError as e:
            logger.warning(f"HTTP error downloading transcript from {url}: {e.response.status_code}")
            return TranscriptDownloadResult(
                success=False,
                status_code=e.response.status_code,
                error=f"http_{e.response.status_code}",
            )
        except httpx.RequestError as e:
            logger.warning(f"Request error downloading transcript from {url}: {e}")
            return TranscriptDownloadResult(
                success=False,
                error="request_error",
            )
