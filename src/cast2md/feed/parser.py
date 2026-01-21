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
    transcript_type: Optional[str] = None  # MIME type from podcast:transcript
    link: Optional[str] = None
    author: Optional[str] = None


@dataclass
class ParsedFeed:
    """Parsed feed data from RSS."""

    title: str
    description: Optional[str]
    image_url: Optional[str]
    episodes: list[ParsedEpisode]
    author: Optional[str] = None
    link: Optional[str] = None
    categories: list[str] = None


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


def extract_transcript_url(entry: dict) -> tuple[str | None, str | None]:
    """Extract Podcast 2.0 transcript URL and MIME type from RSS entry.

    Looks for podcast:transcript elements. Prefers VTT/SRT formats.

    Args:
        entry: Feedparser entry dict.

    Returns:
        Tuple of (transcript URL, MIME type) or (None, None) if not found.
    """
    # Check for podcast:transcript namespace
    # Feedparser returns a dict for single transcript, list for multiple
    transcripts = entry.get("podcast_transcript")
    if transcripts is None:
        return None, None

    # Normalize to list
    if isinstance(transcripts, dict):
        transcripts = [transcripts]

    if not isinstance(transcripts, list):
        return None, None

    # Preference order for transcript formats
    # VTT and SRT have timestamps which we can use
    preferred_types = ["text/vtt", "application/x-subrip", "text/srt", "application/json", "text/plain", "text/html"]

    # First pass: look for preferred formats
    for preferred in preferred_types:
        for transcript in transcripts:
            url = transcript.get("url")
            t_type = transcript.get("type", "")
            if url and preferred.lower() in t_type.lower():
                return url, t_type

    # Second pass: check URL extension for format hints
    for transcript in transcripts:
        url = transcript.get("url")
        if not url:
            continue
        t_type = transcript.get("type", "")
        url_lower = url.lower()
        if url_lower.endswith(".vtt"):
            return url, t_type or "text/vtt"
        elif url_lower.endswith(".srt"):
            return url, t_type or "application/x-subrip"
        elif url_lower.endswith(".json"):
            return url, t_type or "application/json"
        elif url_lower.endswith(".txt"):
            return url, t_type or "text/plain"

    # Return first if no preferred format found
    if transcripts and transcripts[0].get("url"):
        return transcripts[0]["url"], transcripts[0].get("type")

    return None, None


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


def extract_categories(feed: dict) -> list[str]:
    """Extract categories from feed.

    Args:
        feed: Feedparser feed dict.

    Returns:
        List of category strings.
    """
    categories = []

    # Try itunes:category (can be nested)
    itunes_categories = feed.get("itunes_category")
    if itunes_categories:
        if isinstance(itunes_categories, list):
            for cat in itunes_categories:
                if isinstance(cat, dict):
                    categories.append(cat.get("text", ""))
                elif isinstance(cat, str):
                    categories.append(cat)
        elif isinstance(itunes_categories, dict):
            categories.append(itunes_categories.get("text", ""))
        elif isinstance(itunes_categories, str):
            categories.append(itunes_categories)

    # Try tags (general RSS)
    tags = feed.get("tags", [])
    for tag in tags:
        if isinstance(tag, dict):
            term = tag.get("term")
            if term and term not in categories:
                categories.append(term)

    return [c for c in categories if c]  # Filter empty strings


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

    # Extract extended metadata
    author = feed.get("itunes_author") or feed.get("author")
    link = feed.get("link")
    categories = extract_categories(feed)

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

        # Get episode-specific metadata
        episode_link = entry.get("link")
        episode_author = entry.get("itunes_author") or entry.get("author")

        # Extract transcript URL and MIME type
        transcript_url, transcript_type = extract_transcript_url(entry)

        episode = ParsedEpisode(
            guid=guid,
            title=entry.get("title", "Untitled Episode"),
            description=entry.get("description") or entry.get("summary"),
            audio_url=audio_url,
            duration_seconds=duration,
            published_at=parse_published_date(entry),
            transcript_url=transcript_url,
            transcript_type=transcript_type,
            link=episode_link,
            author=episode_author,
        )
        episodes.append(episode)

    return ParsedFeed(
        title=title,
        description=description,
        image_url=image_url,
        episodes=episodes,
        author=author,
        link=link,
        categories=categories,
    )
