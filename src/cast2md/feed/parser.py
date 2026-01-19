"""RSS feed parsing utilities."""

import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Optional

import feedparser


@dataclass
class ParsedEpisode:
    """Parsed episode data from RSS feed."""

    guid: str
    title: str
    description: Optional[str]
    audio_url: str
    duration_seconds: Optional[int]
    published_at: Optional[datetime]
    transcript_url: Optional[str]


@dataclass
class ParsedFeed:
    """Parsed feed data from RSS."""

    title: str
    description: Optional[str]
    image_url: Optional[str]
    episodes: list[ParsedEpisode]


def parse_duration(duration_str: str | None) -> int | None:
    """Parse iTunes duration format to seconds.

    Handles formats:
    - "HH:MM:SS"
    - "MM:SS"
    - "SSSSS" (seconds only)

    Args:
        duration_str: Duration string from iTunes tag.

    Returns:
        Duration in seconds or None if parsing fails.
    """
    if not duration_str:
        return None

    duration_str = duration_str.strip()

    # Try integer seconds
    if duration_str.isdigit():
        return int(duration_str)

    # Try HH:MM:SS or MM:SS format
    parts = duration_str.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = map(int, parts)
            return hours * 3600 + minutes * 60 + seconds
        elif len(parts) == 2:
            minutes, seconds = map(int, parts)
            return minutes * 60 + seconds
    except ValueError:
        pass

    return None


def extract_audio_url(entry: dict) -> str | None:
    """Extract audio URL from RSS entry.

    Checks enclosures first, then media:content.

    Args:
        entry: Feedparser entry dict.

    Returns:
        Audio URL or None if not found.
    """
    # Check enclosures (standard RSS)
    for enclosure in entry.get("enclosures", []):
        enc_type = enclosure.get("type", "")
        enc_url = enclosure.get("href") or enclosure.get("url")

        if enc_url and (
            "audio" in enc_type
            or enc_url.lower().endswith((".mp3", ".m4a", ".wav", ".ogg", ".opus"))
        ):
            return enc_url

    # Check media:content
    media_content = entry.get("media_content", [])
    for media in media_content:
        media_type = media.get("type", "")
        media_url = media.get("url")

        if media_url and (
            "audio" in media_type
            or media_url.lower().endswith((".mp3", ".m4a", ".wav", ".ogg", ".opus"))
        ):
            return media_url

    return None


def extract_transcript_url(entry: dict) -> str | None:
    """Extract Podcast 2.0 transcript URL from RSS entry.

    Looks for podcast:transcript elements.

    Args:
        entry: Feedparser entry dict.

    Returns:
        Transcript URL or None if not found.
    """
    # Check for podcast:transcript namespace
    # Feedparser returns a dict for single transcript, list for multiple
    transcripts = entry.get("podcast_transcript")
    if transcripts is None:
        return None

    # Normalize to list
    if isinstance(transcripts, dict):
        transcripts = [transcripts]

    if not isinstance(transcripts, list):
        return None

    for transcript in transcripts:
        url = transcript.get("url")
        if url:
            # Prefer SRT or VTT formats
            t_type = transcript.get("type", "")
            if "srt" in t_type or "vtt" in t_type or "text" in t_type:
                return url

    # Return first if no preferred format found
    if transcripts and transcripts[0].get("url"):
        return transcripts[0]["url"]

    return None


def parse_published_date(entry: dict) -> datetime | None:
    """Parse published date from RSS entry.

    Args:
        entry: Feedparser entry dict.

    Returns:
        Datetime or None if parsing fails.
    """
    # Try published_parsed first (feedparser's parsed version)
    if entry.get("published_parsed"):
        try:
            return datetime(*entry["published_parsed"][:6])
        except (TypeError, ValueError):
            pass

    # Try published string
    published = entry.get("published") or entry.get("pubDate")
    if published:
        try:
            return parsedate_to_datetime(published)
        except (TypeError, ValueError):
            pass

    return None


def parse_feed(feed_content: str) -> ParsedFeed:
    """Parse RSS feed content.

    Args:
        feed_content: Raw RSS/XML content.

    Returns:
        ParsedFeed with feed metadata and episodes.

    Raises:
        ValueError: If feed cannot be parsed or has no entries.
    """
    parsed = feedparser.parse(feed_content)

    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Failed to parse feed: {parsed.bozo_exception}")

    feed = parsed.feed

    # Extract feed metadata
    title = feed.get("title", "Unknown Podcast")
    description = feed.get("description") or feed.get("subtitle")
    image_url = None

    # Try various image locations
    if feed.get("image"):
        image_url = feed["image"].get("href") or feed["image"].get("url")
    if not image_url and feed.get("itunes_image"):
        image_url = feed["itunes_image"].get("href")

    # Parse episodes
    episodes = []
    for entry in parsed.entries:
        audio_url = extract_audio_url(entry)
        if not audio_url:
            continue  # Skip entries without audio

        # Get GUID, falling back to audio URL
        guid = entry.get("id") or entry.get("guid") or audio_url

        # Get duration from iTunes
        duration = parse_duration(
            entry.get("itunes_duration") or entry.get("duration")
        )

        episode = ParsedEpisode(
            guid=guid,
            title=entry.get("title", "Untitled Episode"),
            description=entry.get("description") or entry.get("summary"),
            audio_url=audio_url,
            duration_seconds=duration,
            published_at=parse_published_date(entry),
            transcript_url=extract_transcript_url(entry),
        )
        episodes.append(episode)

    return ParsedFeed(
        title=title,
        description=description,
        image_url=image_url,
        episodes=episodes,
    )
