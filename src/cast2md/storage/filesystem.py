"""Filesystem utilities for managing podcast storage."""

import re
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from cast2md.config.settings import get_settings


def sanitize_filename(name: str, max_length: int = 100) -> str:
    """Sanitize a string for use as a filename.

    Args:
        name: The string to sanitize.
        max_length: Maximum length of the result.

    Returns:
        A safe filename string.
    """
    # Normalize unicode characters
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")

    # Replace problematic characters with underscores
    name = re.sub(r'[<>:"/\\|?*]', "_", name)

    # Replace multiple spaces/underscores with single underscore
    name = re.sub(r"[\s_]+", "_", name)

    # Remove leading/trailing underscores and dots
    name = name.strip("_.")

    # Truncate if too long
    if len(name) > max_length:
        name = name[:max_length].rstrip("_.")

    return name or "unnamed"


def sanitize_podcast_name(name: str) -> str:
    """Sanitize a podcast name for use as a directory name.

    Args:
        name: The podcast title.

    Returns:
        A safe directory name.
    """
    return sanitize_filename(name, max_length=80)


def episode_filename(title: str, published_at: datetime | None, audio_url: str) -> str:
    """Generate a filename for an episode.

    Format: {YYYY-MM-DD}_{sanitized_title}.{ext}

    Args:
        title: Episode title.
        published_at: Episode publish date.
        audio_url: URL to determine file extension.

    Returns:
        Formatted filename.
    """
    # Get date prefix
    if published_at:
        date_str = published_at.strftime("%Y-%m-%d")
    else:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")

    # Sanitize title
    safe_title = sanitize_filename(title, max_length=80)

    # Extract extension from URL
    parsed = urlparse(audio_url)
    path = parsed.path.lower()

    if ".mp3" in path:
        ext = "mp3"
    elif ".m4a" in path:
        ext = "m4a"
    elif ".wav" in path:
        ext = "wav"
    elif ".ogg" in path:
        ext = "ogg"
    elif ".opus" in path:
        ext = "opus"
    else:
        ext = "mp3"  # Default

    return f"{date_str}_{safe_title}.{ext}"


def get_audio_path(podcast_name: str, episode_title: str,
                   published_at: datetime | None, audio_url: str) -> Path:
    """Get the full path for storing an episode's audio file.

    Structure: {storage_path}/audio/{podcast_name}/{filename}

    Args:
        podcast_name: Name of the podcast.
        episode_title: Episode title.
        published_at: Episode publish date.
        audio_url: Audio URL for extension detection.

    Returns:
        Full path to the audio file.
    """
    settings = get_settings()
    safe_podcast = sanitize_podcast_name(podcast_name)
    filename = episode_filename(episode_title, published_at, audio_url)

    return settings.storage_path / "audio" / safe_podcast / filename


def get_transcript_path(podcast_name: str, episode_title: str,
                        published_at: datetime | None) -> Path:
    """Get the full path for storing an episode's transcript.

    Structure: {storage_path}/transcripts/{podcast_name}/{filename}

    Args:
        podcast_name: Name of the podcast.
        episode_title: Episode title.
        published_at: Episode publish date.

    Returns:
        Full path to the transcript file (markdown).
    """
    settings = get_settings()
    safe_podcast = sanitize_podcast_name(podcast_name)

    # Generate filename similar to audio but with .md extension
    if published_at:
        date_str = published_at.strftime("%Y-%m-%d")
    else:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")

    safe_title = sanitize_filename(episode_title, max_length=80)
    filename = f"{date_str}_{safe_title}.md"

    return settings.storage_path / "transcripts" / safe_podcast / filename


def ensure_podcast_directories(podcast_name: str) -> tuple[Path, Path]:
    """Create the directory structure for a podcast.

    Structure:
        {storage_path}/audio/{podcast_name}/
        {storage_path}/transcripts/{podcast_name}/

    Args:
        podcast_name: Name of the podcast.

    Returns:
        Tuple of (audio_dir, transcripts_dir).
    """
    settings = get_settings()
    safe_podcast = sanitize_podcast_name(podcast_name)

    audio_dir = settings.storage_path / "audio" / safe_podcast
    transcripts_dir = settings.storage_path / "transcripts" / safe_podcast

    audio_dir.mkdir(parents=True, exist_ok=True)
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    return audio_dir, transcripts_dir


def get_temp_download_path(filename: str) -> Path:
    """Get a temporary path for downloading.

    Args:
        filename: The target filename.

    Returns:
        Path in the temp directory.
    """
    settings = get_settings()
    settings.temp_download_path.mkdir(parents=True, exist_ok=True)
    return settings.temp_download_path / f".downloading_{filename}"


def rename_podcast_directories(old_name: str, new_name: str) -> bool:
    """Rename podcast directories when custom_title changes.

    Renames:
        {storage_path}/audio/{old_name}/ → {storage_path}/audio/{new_name}/
        {storage_path}/transcripts/{old_name}/ → {storage_path}/transcripts/{new_name}/

    Args:
        old_name: The old podcast name (display title).
        new_name: The new podcast name (display title).

    Returns:
        True if any directories were renamed, False if source didn't exist.

    Raises:
        OSError: If target directory already exists.
    """
    settings = get_settings()
    safe_old = sanitize_podcast_name(old_name)
    safe_new = sanitize_podcast_name(new_name)

    if safe_old == safe_new:
        return False  # No change needed

    renamed = False
    for subdir in ["audio", "transcripts"]:
        old_path = settings.storage_path / subdir / safe_old
        new_path = settings.storage_path / subdir / safe_new

        if old_path.exists():
            if new_path.exists():
                raise OSError(f"Target directory already exists: {new_path}")
            old_path.rename(new_path)
            renamed = True

    return renamed
