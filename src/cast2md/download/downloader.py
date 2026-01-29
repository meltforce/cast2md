"""Audio file download module."""

import logging
import shutil
from pathlib import Path

import httpx

from cast2md.config.settings import get_settings
from cast2md.db.connection import get_db
from cast2md.db.models import Episode, EpisodeStatus, Feed
from cast2md.db.repository import EpisodeRepository
from cast2md.storage.filesystem import (
    ensure_podcast_directories,
    episode_filename,
    get_temp_download_path,
)

logger = logging.getLogger(__name__)

# Audio file magic bytes for validation
AUDIO_SIGNATURES = {
    b"\xff\xfb": "mp3",  # MP3 frame sync
    b"\xff\xfa": "mp3",  # MP3 frame sync
    b"\xff\xf3": "mp3",  # MP3 frame sync
    b"\xff\xf2": "mp3",  # MP3 frame sync
    b"ID3": "mp3",  # MP3 with ID3 tag
    b"ftyp": "m4a",  # M4A/MP4 (offset 4)
    b"OggS": "ogg",  # Ogg container
    b"RIFF": "wav",  # WAV file
}


def validate_audio_header(data: bytes) -> bool:
    """Validate that data looks like an audio file.

    Args:
        data: First bytes of the file.

    Returns:
        True if it appears to be a valid audio file.
    """
    if len(data) < 12:
        return False

    # Check MP3 signatures
    if data[:3] == b"ID3":
        return True
    if data[:2] in (b"\xff\xfb", b"\xff\xfa", b"\xff\xf3", b"\xff\xf2"):
        return True

    # Check M4A (ftyp at offset 4)
    if data[4:8] == b"ftyp":
        return True

    # Check Ogg
    if data[:4] == b"OggS":
        return True

    # Check WAV
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return True

    return False


def refresh_audio_url_from_feed(feed: Feed, episode_guid: str) -> str | None:
    """Fetch fresh audio URL from RSS feed for an episode.

    Some podcast hosts use signed/expiring URLs for audio files.
    This function fetches the current feed and extracts the fresh
    audio URL for the given episode GUID.

    Args:
        feed: Feed to fetch.
        episode_guid: GUID of the episode to find.

    Returns:
        Fresh audio URL or None if not found.
    """
    from cast2md.feed.parser import parse_feed

    settings = get_settings()

    try:
        with httpx.Client(timeout=settings.request_timeout) as client:
            response = client.get(
                feed.url,
                follow_redirects=True,
                headers={"User-Agent": settings.user_agent},
            )
            response.raise_for_status()

        parsed = parse_feed(response.text)

        # Find episode by GUID
        for ep in parsed.episodes:
            if ep.guid == episode_guid:
                return ep.audio_url

        logger.warning(f"Episode GUID {episode_guid} not found in feed")
        return None

    except Exception as e:
        logger.warning(f"Failed to refresh audio URL from feed: {e}")
        return None


async def download_file(url: str, dest_path: Path, temp_path: Path) -> None:
    """Download a file with streaming and atomic move.

    Downloads to temp location first, then moves to final destination.

    Args:
        url: URL to download from.
        dest_path: Final destination path.
        temp_path: Temporary download path.

    Raises:
        httpx.HTTPError: If download fails.
        ValueError: If downloaded file is not valid audio.
    """
    settings = get_settings()

    # Ensure temp directory exists
    temp_path.parent.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "GET",
            url,
            follow_redirects=True,
            timeout=httpx.Timeout(
                connect=settings.request_timeout,
                read=300.0,  # 5 min read timeout for large files
                write=30.0,
                pool=30.0,
            ),
            headers={
                "User-Agent": settings.user_agent
            },
        ) as response:
            response.raise_for_status()

            # Stream to temp file
            first_chunk = True
            with open(temp_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    if first_chunk:
                        if not validate_audio_header(chunk):
                            raise ValueError("Downloaded file does not appear to be audio")
                        first_chunk = False
                    f.write(chunk)

    # Ensure destination directory exists
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic move to final destination
    shutil.move(str(temp_path), str(dest_path))


def download_file_sync(url: str, dest_path: Path, temp_path: Path) -> None:
    """Synchronously download a file with streaming and atomic move.

    Args:
        url: URL to download from.
        dest_path: Final destination path.
        temp_path: Temporary download path.

    Raises:
        httpx.HTTPError: If download fails.
        ValueError: If downloaded file is not valid audio.
    """
    settings = get_settings()

    # Ensure temp directory exists
    temp_path.parent.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=None) as client:
        with client.stream(
            "GET",
            url,
            follow_redirects=True,
            timeout=httpx.Timeout(
                connect=settings.request_timeout,
                read=300.0,
                write=30.0,
                pool=30.0,
            ),
            headers={
                "User-Agent": settings.user_agent
            },
        ) as response:
            response.raise_for_status()

            first_chunk = True
            with open(temp_path, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=65536):
                    if first_chunk:
                        if not validate_audio_header(chunk):
                            raise ValueError("Downloaded file does not appear to be audio")
                        first_chunk = False
                    f.write(chunk)

    # Ensure destination directory exists
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic move to final destination
    shutil.move(str(temp_path), str(dest_path))


def download_episode(episode: Episode, feed: Feed) -> Path:
    """Download an episode's audio file.

    Before downloading, refreshes the audio URL from the feed to handle
    podcasts that use signed/expiring URLs.

    Args:
        episode: Episode to download.
        feed: Feed the episode belongs to.

    Returns:
        Path to the downloaded audio file.

    Raises:
        Exception: If download fails.
    """
    # Ensure podcast directories exist
    audio_dir, _ = ensure_podcast_directories(feed.title)

    # Refresh audio URL from feed (handles expiring signed URLs)
    audio_url = episode.audio_url
    fresh_url = refresh_audio_url_from_feed(feed, episode.guid)
    if fresh_url and fresh_url != audio_url:
        logger.info(f"Refreshed audio URL for episode {episode.id}")
        audio_url = fresh_url

    # Generate filename and paths
    filename = episode_filename(
        episode.title,
        episode.published_at,
        audio_url,
    )
    dest_path = audio_dir / filename
    temp_path = get_temp_download_path(filename)

    with get_db() as conn:
        repo = EpisodeRepository(conn)

        # Update status to downloading
        repo.update_status(episode.id, EpisodeStatus.DOWNLOADING)

        # Update stored audio URL if it changed
        if fresh_url and fresh_url != episode.audio_url:
            repo.update_audio_url(episode.id, fresh_url)

        try:
            # Download the file
            download_file_sync(audio_url, dest_path, temp_path)

            # Update episode with path and status
            repo.update_audio_path(episode.id, str(dest_path))
            repo.update_status(episode.id, EpisodeStatus.AUDIO_READY)

            return dest_path

        except Exception as e:
            # Clean up temp file if it exists
            if temp_path.exists():
                temp_path.unlink()

            repo.update_status(
                episode.id,
                EpisodeStatus.FAILED,
                error_message=f"Failed to download audio",
            )
            raise
