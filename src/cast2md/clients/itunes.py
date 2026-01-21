"""iTunes API client for podcast lookup."""

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from cast2md.config.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ItunesPodcast:
    """Podcast metadata from iTunes API."""

    itunes_id: str
    title: str
    author: str
    feed_url: Optional[str]
    artwork_url: Optional[str] = None


class ItunesClient:
    """Client for iTunes Lookup API.

    Provides methods to look up podcast metadata by iTunes ID.
    """

    BASE_URL = "https://itunes.apple.com"

    def __init__(self, timeout: Optional[float] = None):
        """Initialize client.

        Args:
            timeout: Request timeout in seconds. Defaults to settings value.
        """
        settings = get_settings()
        self.timeout = timeout or settings.request_timeout
        self.user_agent = settings.user_agent

    def lookup(self, itunes_id: str) -> Optional[ItunesPodcast]:
        """Look up podcast by iTunes ID.

        Args:
            itunes_id: The iTunes ID (e.g., "1200361736" from an Apple Podcasts URL).

        Returns:
            ItunesPodcast with metadata, or None if not found.
        """
        url = f"{self.BASE_URL}/lookup"
        params = {"id": itunes_id}

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    url,
                    params=params,
                    headers={"User-Agent": self.user_agent},
                )
                response.raise_for_status()

            data = response.json()

            if data.get("resultCount", 0) == 0:
                logger.debug(f"No results for iTunes ID: {itunes_id}")
                return None

            result = data["results"][0]

            return ItunesPodcast(
                itunes_id=itunes_id,
                title=result.get("collectionName", ""),
                author=result.get("artistName", ""),
                feed_url=result.get("feedUrl"),
                artwork_url=result.get("artworkUrl100"),
            )

        except httpx.HTTPStatusError as e:
            logger.warning(f"HTTP error looking up iTunes ID {itunes_id}: {e.response.status_code}")
            return None
        except httpx.RequestError as e:
            logger.warning(f"Request error looking up iTunes ID {itunes_id}: {e}")
            return None
        except (KeyError, IndexError, ValueError) as e:
            logger.warning(f"Error parsing iTunes response for ID {itunes_id}: {e}")
            return None

    def search(self, term: str, limit: int = 25) -> list[ItunesPodcast]:
        """Search podcasts by term.

        Args:
            term: Search term.
            limit: Maximum results to return.

        Returns:
            List of matching podcasts.
        """
        settings = get_settings()
        url = f"{self.BASE_URL}/search"
        params = {
            "term": term,
            "media": "podcast",
            "country": settings.itunes_country,
            "limit": limit,
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    url,
                    params=params,
                    headers={"User-Agent": self.user_agent},
                )
                response.raise_for_status()

            data = response.json()
            results = []

            for result in data.get("results", []):
                itunes_id = str(result.get("collectionId", ""))
                if not itunes_id:
                    continue

                results.append(
                    ItunesPodcast(
                        itunes_id=itunes_id,
                        title=result.get("collectionName", ""),
                        author=result.get("artistName", ""),
                        feed_url=result.get("feedUrl"),
                        artwork_url=result.get("artworkUrl100"),
                    )
                )

            return results

        except httpx.HTTPStatusError as e:
            logger.warning(f"HTTP error searching iTunes: {e.response.status_code}")
            return []
        except httpx.RequestError as e:
            logger.warning(f"Request error searching iTunes: {e}")
            return []
        except (KeyError, ValueError) as e:
            logger.warning(f"Error parsing iTunes search response: {e}")
            return []
