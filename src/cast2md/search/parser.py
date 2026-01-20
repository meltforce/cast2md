"""Parse transcript markdown files to extract segments with timestamps."""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TranscriptSegment:
    """A segment of transcript text with timing information."""

    text: str
    start: float  # Start time in seconds
    end: float  # End time in seconds


def parse_timestamp(ts: str) -> float:
    """Parse timestamp string to seconds.

    Supports formats:
    - MM:SS
    - HH:MM:SS

    Args:
        ts: Timestamp string like "01:30" or "1:05:30"

    Returns:
        Time in seconds as float.
    """
    parts = ts.split(":")
    if len(parts) == 2:
        # MM:SS
        minutes, seconds = int(parts[0]), int(parts[1])
        return minutes * 60 + seconds
    elif len(parts) == 3:
        # HH:MM:SS
        hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
        return hours * 3600 + minutes * 60 + seconds
    else:
        return 0.0


def parse_transcript_segments(content: str) -> list[TranscriptSegment]:
    """Parse markdown transcript content into segments.

    Expected format:
    ```
    # Episode Title

    *Language: en (100.0% confidence)*

    **[00:00]** First segment text

    **[00:05]** Second segment text
    ```

    Args:
        content: Markdown transcript content.

    Returns:
        List of TranscriptSegment objects with text and timing.
    """
    segments = []

    # Pattern to match timestamp lines: **[MM:SS]** or **[HH:MM:SS]**
    # Captures timestamp and following text
    pattern = r'\*\*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\*\*\s*(.+?)(?=\*\*\[|\Z)'

    matches = re.findall(pattern, content, re.DOTALL)

    for i, (timestamp, text) in enumerate(matches):
        start = parse_timestamp(timestamp)

        # End time is start of next segment, or start + 30s for last segment
        if i + 1 < len(matches):
            end = parse_timestamp(matches[i + 1][0])
        else:
            end = start + 30.0  # Default duration for last segment

        # Clean up text: remove extra whitespace
        cleaned_text = " ".join(text.split())

        if cleaned_text:  # Only add non-empty segments
            segments.append(TranscriptSegment(
                text=cleaned_text,
                start=start,
                end=end,
            ))

    return segments


def parse_transcript_file(path: Path) -> list[TranscriptSegment]:
    """Parse a transcript file into segments.

    Args:
        path: Path to markdown transcript file.

    Returns:
        List of TranscriptSegment objects.

    Raises:
        FileNotFoundError: If file doesn't exist.
    """
    content = path.read_text(encoding="utf-8")
    return parse_transcript_segments(content)
