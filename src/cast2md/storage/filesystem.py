"""Filesystem utilities for managing podcast storage."""

import logging
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from cast2md.config.settings import get_settings

logger = logging.getLogger(__name__)


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
        date_str = datetime.now().strftime("%Y-%m-%d")

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
        date_str = datetime.now().strftime("%Y-%m-%d")

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


def get_trash_path() -> Path:
    """Get the trash directory path."""
    settings = get_settings()
    return settings.storage_path / "trash"


def move_feed_to_trash(feed_id: int, feed_title: str) -> Path | None:
    """Move audio/ and transcripts/ subdirs for a feed to trash.

    Structure: {storage_path}/trash/{feed_slug}_{feed_id}_{timestamp}/

    Args:
        feed_id: The feed ID.
        feed_title: The feed title (for naming the trash folder).

    Returns:
        Path to the trash folder if files were moved, None if no files existed.
    """
    settings = get_settings()
    safe_name = sanitize_podcast_name(feed_title)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    trash_dir = get_trash_path() / f"{safe_name}_{feed_id}_{timestamp}"

    audio_dir = settings.storage_path / "audio" / safe_name
    transcripts_dir = settings.storage_path / "transcripts" / safe_name

    moved = False

    # Move audio directory if it exists
    if audio_dir.exists() and any(audio_dir.iterdir()):
        trash_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(audio_dir), str(trash_dir / "audio"))
        moved = True
        logger.info(f"Moved audio to trash: {trash_dir / 'audio'}")

    # Move transcripts directory if it exists
    if transcripts_dir.exists() and any(transcripts_dir.iterdir()):
        trash_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(transcripts_dir), str(trash_dir / "transcripts"))
        moved = True
        logger.info(f"Moved transcripts to trash: {trash_dir / 'transcripts'}")

    # Clean up empty parent directories
    for dir_path in [audio_dir, transcripts_dir]:
        if dir_path.exists() and not any(dir_path.iterdir()):
            dir_path.rmdir()

    return trash_dir if moved else None


def cleanup_old_trash(days: int = 30) -> int:
    """Delete trash entries older than specified days.

    Args:
        days: Delete trash folders older than this many days.

    Returns:
        Number of trash entries deleted.
    """
    trash_path = get_trash_path()
    if not trash_path.exists():
        return 0

    cutoff = datetime.now().timestamp() - (days * 24 * 60 * 60)
    deleted = 0

    for entry in trash_path.iterdir():
        if entry.is_dir() and entry.stat().st_mtime < cutoff:
            shutil.rmtree(entry)
            logger.info(f"Deleted old trash: {entry.name}")
            deleted += 1

    return deleted


def cleanup_orphaned_temp_files(hours: int = 24) -> int:
    """Delete orphaned temporary files older than specified hours.

    Cleans up:
    - preprocess_*.wav - Preprocessing temp files
    - .downloading_* - Incomplete downloads
    - chunk_*.wav - Transcription chunk files

    These files can be orphaned if the server crashes or workers are interrupted.

    Args:
        hours: Delete temp files older than this many hours.

    Returns:
        Number of files deleted.
    """
    settings = get_settings()
    temp_dir = settings.temp_download_path

    if not temp_dir.exists():
        return 0

    cutoff = datetime.now().timestamp() - (hours * 60 * 60)
    deleted = 0

    # Patterns for orphaned temp files
    patterns = ["preprocess_*.wav", ".downloading_*", "chunk_*.wav"]

    for pattern in patterns:
        for file_path in temp_dir.glob(pattern):
            if file_path.is_file():
                try:
                    if file_path.stat().st_mtime < cutoff:
                        file_path.unlink()
                        logger.info(f"Deleted orphaned temp file: {file_path.name}")
                        deleted += 1
                except Exception as e:
                    logger.warning(f"Failed to delete temp file {file_path}: {e}")

    return deleted
