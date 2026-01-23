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


def merge_word_level_segments(
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
                text=" ".join(current_texts),
                start=current_start,
                end=current_end,
            ))
            current_texts = []
            current_start = seg.start

        current_texts.append(text)
        current_end = seg.end

    # Don't forget the last phrase
    if current_texts:
        merged.append(TranscriptSegment(
            text=" ".join(current_texts),
            start=current_start,
            end=current_end,
        ))

    return merged


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
