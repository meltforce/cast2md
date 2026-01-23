"""Transcript format converters for external transcript sources.

Converts VTT, SRT, JSON, and text transcript formats to markdown.
"""

import json
import logging
import re
from dataclasses import dataclass
from html import unescape
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TranscriptSegment:
    """A segment of transcript with timing info."""

    start: float  # Start time in seconds
    end: float  # End time in seconds
    text: str


def _format_timestamp(seconds: float) -> str:
    """Format seconds as MM:SS or HH:MM:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _merge_word_level_segments(
    segments: list[TranscriptSegment],
    min_phrase_chars: int = 40,
    max_phrase_chars: int = 200,
    pause_threshold: float = 1.5,
) -> list[TranscriptSegment]:
    """Merge word-level segments into phrase-level segments.

    Transcripts (both external and Whisper) can have word-level timestamps
    where each word is a separate cue. This function merges consecutive
    short segments into natural phrases while preserving long segments.

    Handles mixed transcripts where some portions are phrase-level and
    others are word-level.

    Merging stops when:
    - The phrase reaches min_phrase_chars and ends with punctuation
    - The phrase reaches max_phrase_chars
    - There's a pause (gap) of pause_threshold seconds between segments
    - The next segment is already long enough (>= min_phrase_chars)

    Args:
        segments: List of transcript segments (potentially word-level).
        min_phrase_chars: Minimum characters before considering phrase complete.
        max_phrase_chars: Maximum characters per phrase.
        pause_threshold: Seconds of pause that indicates a natural break.

    Returns:
        List of merged transcript segments.
    """
    if not segments:
        return []

    merged = []
    current_texts: list[str] = []
    current_start = segments[0].start
    current_end = segments[0].end

    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue

        seg_len = len(text)

        # Check if we should start a new phrase
        start_new = False

        if current_texts:
            # Check for pause between segments
            gap = seg.start - current_end
            if gap > pause_threshold:
                start_new = True

            # Check if phrase is getting too long
            current_len = sum(len(t) for t in current_texts) + len(current_texts)  # +spaces
            if current_len >= max_phrase_chars:
                start_new = True

            # Check if phrase is complete (min length + ends with punctuation)
            if current_len >= min_phrase_chars:
                last_text = current_texts[-1] if current_texts else ""
                if last_text and last_text[-1] in ".!?":
                    start_new = True

            # If the incoming segment is already long, save current and start fresh
            if seg_len >= min_phrase_chars and current_len >= min_phrase_chars:
                start_new = True

        if start_new and current_texts:
            # Save the current phrase
            merged.append(TranscriptSegment(
                start=current_start,
                end=current_end,
                text=" ".join(current_texts),
            ))
            current_texts = []
            current_start = seg.start

        current_texts.append(text)
        current_end = seg.end

    # Don't forget the last phrase
    if current_texts:
        merged.append(TranscriptSegment(
            start=current_start,
            end=current_end,
            text=" ".join(current_texts),
        ))

    return merged


def _segments_to_markdown(
    segments: list[TranscriptSegment],
    title: str = "",
    source_info: str = "",
) -> str:
    """Convert transcript segments to markdown format.

    Args:
        segments: List of transcript segments.
        title: Optional episode title.
        source_info: Optional source information (e.g., "Downloaded from publisher").

    Returns:
        Markdown formatted transcript.
    """
    lines = []

    if title:
        lines.append(f"# {title}")
        lines.append("")

    if source_info:
        lines.append(f"*{source_info}*")
        lines.append("")

    # Merge word-level segments into phrases for better readability
    merged_segments = _merge_word_level_segments(segments)

    if merged_segments:
        for seg in merged_segments:
            timestamp = _format_timestamp(seg.start)
            lines.append(f"**[{timestamp}]** {seg.text.strip()}")
            lines.append("")
    else:
        lines.append("*No transcript content available*")
        lines.append("")

    return "\n".join(lines)


def _parse_vtt_timestamp(ts: str) -> float:
    """Parse VTT timestamp to seconds.

    VTT format: HH:MM:SS.mmm or MM:SS.mmm
    """
    ts = ts.strip()

    # Handle HH:MM:SS.mmm
    match = re.match(r"(\d+):(\d+):(\d+)\.?(\d*)", ts)
    if match:
        h, m, s, ms = match.groups()
        ms = ms.ljust(3, "0")[:3] if ms else "0"
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

    # Handle MM:SS.mmm
    match = re.match(r"(\d+):(\d+)\.?(\d*)", ts)
    if match:
        m, s, ms = match.groups()
        ms = ms.ljust(3, "0")[:3] if ms else "0"
        return int(m) * 60 + int(s) + int(ms) / 1000

    return 0.0


def _parse_srt_timestamp(ts: str) -> float:
    """Parse SRT timestamp to seconds.

    SRT format: HH:MM:SS,mmm
    """
    ts = ts.strip().replace(",", ".")
    return _parse_vtt_timestamp(ts)


def parse_vtt(content: str) -> list[TranscriptSegment]:
    """Parse WebVTT content to segments.

    Args:
        content: Raw VTT content.

    Returns:
        List of transcript segments.
    """
    segments = []
    lines = content.split("\n")

    i = 0
    # Skip WEBVTT header and metadata
    while i < len(lines) and not re.match(r"\d+:\d+", lines[i]):
        i += 1

    while i < len(lines):
        line = lines[i].strip()

        # Look for timestamp line: 00:00:00.000 --> 00:00:05.000
        match = re.match(r"([\d:.]+)\s*-->\s*([\d:.]+)", line)
        if match:
            start = _parse_vtt_timestamp(match.group(1))
            end = _parse_vtt_timestamp(match.group(2))

            # Collect text lines until empty line or next timestamp
            i += 1
            text_lines = []
            while i < len(lines):
                text_line = lines[i].strip()
                if not text_line:
                    break
                if re.match(r"[\d:.]+\s*-->", text_line):
                    # Don't advance, let outer loop handle this
                    i -= 1
                    break
                # Remove VTT tags like <v Speaker>
                text_line = re.sub(r"<[^>]+>", "", text_line)
                text_lines.append(text_line)
                i += 1

            text = " ".join(text_lines).strip()
            if text:
                # Unescape HTML entities
                text = unescape(text)
                segments.append(TranscriptSegment(start=start, end=end, text=text))

        i += 1

    return segments


def parse_srt(content: str) -> list[TranscriptSegment]:
    """Parse SRT (SubRip) content to segments.

    Args:
        content: Raw SRT content.

    Returns:
        List of transcript segments.
    """
    segments = []

    # Split into blocks separated by blank lines
    blocks = re.split(r"\n\s*\n", content.strip())

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 2:
            continue

        # Find timestamp line (may be first or second line)
        timestamp_line = None
        text_start = 0
        for idx, line in enumerate(lines[:2]):
            if "-->" in line:
                timestamp_line = line
                text_start = idx + 1
                break

        if not timestamp_line:
            continue

        # Parse timestamp: 00:00:00,000 --> 00:00:05,000
        match = re.match(r"([\d:,]+)\s*-->\s*([\d:,]+)", timestamp_line)
        if not match:
            continue

        start = _parse_srt_timestamp(match.group(1))
        end = _parse_srt_timestamp(match.group(2))

        # Collect text
        text_lines = lines[text_start:]
        text = " ".join(line.strip() for line in text_lines if line.strip())

        # Remove HTML tags (some SRTs have formatting)
        text = re.sub(r"<[^>]+>", "", text)
        text = unescape(text)

        if text:
            segments.append(TranscriptSegment(start=start, end=end, text=text))

    return segments


def parse_podcasting_json(content: str) -> list[TranscriptSegment]:
    """Parse Podcasting 2.0 JSON transcript format.

    Format: {"segments": [{"startTime": 0.0, "endTime": 5.0, "body": "text"}, ...]}
    or: [{"startTime": 0.0, "endTime": 5.0, "body": "text"}, ...]

    Args:
        content: Raw JSON content.

    Returns:
        List of transcript segments.
    """
    segments = []

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("Failed to parse JSON transcript")
        return segments

    # Handle both object wrapper and direct array
    if isinstance(data, dict):
        segment_list = data.get("segments", [])
    elif isinstance(data, list):
        segment_list = data
    else:
        return segments

    for item in segment_list:
        if not isinstance(item, dict):
            continue

        # Handle various field name conventions
        start = item.get("startTime") or item.get("start") or item.get("startMs", 0) / 1000
        end = item.get("endTime") or item.get("end") or item.get("endMs", 0) / 1000
        text = item.get("body") or item.get("text") or item.get("content", "")

        if isinstance(start, str):
            start = float(start)
        if isinstance(end, str):
            end = float(end)

        text = str(text).strip()
        if text:
            segments.append(TranscriptSegment(start=start, end=end, text=text))

    return segments


def parse_plain_text(content: str, title: str = "") -> str:
    """Convert plain text transcript to markdown (no timestamps).

    Args:
        content: Raw text content.
        title: Optional episode title.

    Returns:
        Markdown formatted transcript.
    """
    lines = []

    if title:
        lines.append(f"# {title}")
        lines.append("")

    lines.append("*Source: Downloaded from publisher (no timestamps)*")
    lines.append("")

    # Split into paragraphs and format
    paragraphs = re.split(r"\n\s*\n", content.strip())
    for para in paragraphs:
        para = para.strip()
        if para:
            lines.append(para)
            lines.append("")

    return "\n".join(lines)


def parse_html(content: str) -> str:
    """Extract text from HTML content.

    Args:
        content: Raw HTML content.

    Returns:
        Plain text extracted from HTML.
    """
    # Remove script and style elements
    content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL | re.IGNORECASE)
    content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL | re.IGNORECASE)

    # Replace block elements with newlines
    content = re.sub(r"<(p|div|br|h[1-6]|li)[^>]*>", "\n", content, flags=re.IGNORECASE)

    # Remove remaining tags
    content = re.sub(r"<[^>]+>", "", content)

    # Unescape HTML entities
    content = unescape(content)

    # Normalize whitespace
    content = re.sub(r"\n\s*\n+", "\n\n", content)
    content = content.strip()

    return content


def convert_to_markdown(
    content: str,
    mime_type: str,
    title: str = "",
    url: str = "",
) -> tuple[str, str]:
    """Convert transcript content to markdown based on MIME type.

    Args:
        content: Raw transcript content.
        mime_type: MIME type of the content.
        title: Optional episode title.
        url: Original URL (for source info).

    Returns:
        Tuple of (markdown content, format identifier like 'vtt', 'srt', 'json', 'text').
    """
    mime_lower = (mime_type or "").lower()

    # Detect format from MIME type
    if "vtt" in mime_lower or mime_type == "text/vtt":
        segments = parse_vtt(content)
        format_id = "vtt"
    elif "srt" in mime_lower or "subrip" in mime_lower:
        segments = parse_srt(content)
        format_id = "srt"
    elif "json" in mime_lower:
        segments = parse_podcasting_json(content)
        format_id = "json"
    elif "html" in mime_lower:
        text = parse_html(content)
        return parse_plain_text(text, title=title), "html"
    else:
        # Treat as plain text
        return parse_plain_text(content, title=title), "text"

    # Convert segments to markdown
    if segments:
        source_info = "Source: Downloaded from publisher"
        markdown = _segments_to_markdown(segments, title=title, source_info=source_info)
        return markdown, format_id
    else:
        # Fall back to plain text if no segments parsed
        return parse_plain_text(content, title=title), "text"


def detect_format_from_url(url: str) -> Optional[str]:
    """Detect transcript format from URL extension.

    Args:
        url: Transcript URL.

    Returns:
        Detected MIME type or None.
    """
    url_lower = url.lower().split("?")[0]  # Remove query string

    if url_lower.endswith(".vtt"):
        return "text/vtt"
    elif url_lower.endswith(".srt"):
        return "application/x-subrip"
    elif url_lower.endswith(".json"):
        return "application/json"
    elif url_lower.endswith(".txt"):
        return "text/plain"
    elif url_lower.endswith(".html") or url_lower.endswith(".htm"):
        return "text/html"

    return None
