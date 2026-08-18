"""Transcript export formats."""

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TranscriptSegment:
    """A segment of transcribed text with timing."""

    start: float
    end: float
    text: str
    speaker: str | None = None  # Speaker name, when the transcript names one


@dataclass
class ParsedTranscript:
    """Parsed transcript data."""

    title: str
    language: str
    segments: list[TranscriptSegment]
    raw_text: str = ""  # For transcripts without timestamps

    @classmethod
    def from_markdown(cls, content: str) -> "ParsedTranscript":
        """Parse a markdown transcript file.

        Expected format with timestamps:
        # Title

        *Language: en (99.0% confidence)*

        **[00:00]** Text here

        **[00:05]** **Chris:** More text

        The speaker prefix is optional. Or without timestamps (plain paragraphs).
        """
        lines = content.strip().split("\n")

        title = ""
        language = "unknown"
        segments = []
        body_lines = []

        # Parse title
        title_line_idx = -1
        for i, line in enumerate(lines):
            if line.startswith("# "):
                title = line[2:].strip()
                title_line_idx = i
                break

        # Parse language
        lang_line_idx = -1
        lang_pattern = r"\*Language: (\w+)"
        for i, line in enumerate(lines):
            match = re.search(lang_pattern, line)
            if match:
                language = match.group(1)
                lang_line_idx = i
                break

        # Parse segments with timestamps
        # Pattern: **[MM:SS]** or **[HH:MM:SS]**, optional **Speaker:**, then text
        timestamp_pattern = (
            r"\*\*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\*\*\s*"
            r"(?:\*\*([^*\n]{1,60}?):\*\*\s*)?"
            r"(.+)"
        )

        for i, line in enumerate(lines):
            # Skip title and language lines
            if i == title_line_idx or i == lang_line_idx:
                continue

            match = re.match(timestamp_pattern, line)
            if match:
                timestamp_str = match.group(1)
                speaker = (match.group(2) or "").strip() or None
                text = match.group(3).strip()

                # Parse timestamp to seconds
                parts = timestamp_str.split(":")
                if len(parts) == 2:
                    start = int(parts[0]) * 60 + int(parts[1])
                else:
                    start = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

                # Update previous segment's end time
                if segments:
                    segments[-1].end = float(start)

                segments.append(
                    TranscriptSegment(
                        start=float(start),
                        end=float(start + 5),  # Default 5 second duration, will be updated
                        text=text,
                        speaker=speaker,
                    )
                )
            elif line.strip():
                # Collect non-timestamp lines as raw text
                body_lines.append(line.strip())

        raw_text = "\n\n".join(body_lines) if not segments else ""

        return cls(title=title, language=language, segments=segments, raw_text=raw_text)


def _join_paragraph(speaker: str | None, texts: list[str]) -> str:
    """Join a paragraph's texts, prefixed with the speaker when there is one."""
    body = " ".join(texts)
    return f"{speaker}: {body}" if speaker else body


def to_plain_text(transcript: ParsedTranscript) -> str:
    """Convert transcript to plain text (no timestamps).

    A speaker change starts a new paragraph, so the prefix is written once per
    turn rather than once per segment.
    """
    lines = []

    if transcript.title:
        lines.append(transcript.title)
        lines.append("=" * len(transcript.title))
        lines.append("")

    # If no segments, use raw text
    if not transcript.segments and transcript.raw_text:
        lines.append(transcript.raw_text)
        return "\n".join(lines)

    # Group text into paragraphs
    paragraph: list[str] = []
    paragraph_speaker: str | None = None
    for seg in transcript.segments:
        text = seg.text.strip()

        if paragraph and seg.speaker != paragraph_speaker:
            lines.append(_join_paragraph(paragraph_speaker, paragraph))
            lines.append("")
            paragraph = []

        if not paragraph:
            paragraph_speaker = seg.speaker

        paragraph.append(text)
        if text and text[-1] in ".!?":
            lines.append(_join_paragraph(paragraph_speaker, paragraph))
            lines.append("")
            paragraph = []

    if paragraph:
        lines.append(_join_paragraph(paragraph_speaker, paragraph))

    return "\n".join(lines)


def to_srt(transcript: ParsedTranscript) -> str:
    """Convert transcript to SRT subtitle format."""
    lines = []

    for i, seg in enumerate(transcript.segments, 1):
        # Sequence number
        lines.append(str(i))

        # Timestamps: HH:MM:SS,mmm --> HH:MM:SS,mmm
        start = _format_srt_timestamp(seg.start)
        end = _format_srt_timestamp(seg.end)
        lines.append(f"{start} --> {end}")

        # Text
        lines.append(seg.text)

        # Blank line between entries
        lines.append("")

    return "\n".join(lines)


def to_vtt(transcript: ParsedTranscript) -> str:
    """Convert transcript to WebVTT subtitle format.

    A named speaker is written as a voice span, `<v Chris>`, which is how the
    publisher transcripts carry it. SRT has no such construct, so to_srt drops
    the name.
    """
    lines = ["WEBVTT", ""]

    for seg in transcript.segments:
        # Timestamps: HH:MM:SS.mmm --> HH:MM:SS.mmm
        start = _format_vtt_timestamp(seg.start)
        end = _format_vtt_timestamp(seg.end)
        lines.append(f"{start} --> {end}")

        # Text, with the speaker as a voice span when the transcript names one
        lines.append(f"<v {seg.speaker}>{seg.text}" if seg.speaker else seg.text)

        # Blank line between entries
        lines.append("")

    return "\n".join(lines)


def to_json(transcript: ParsedTranscript) -> str:
    """Convert transcript to JSON format."""
    data = {
        "title": transcript.title,
        "language": transcript.language,
    }

    if transcript.segments:
        data["segments"] = [
            {
                "start": seg.start,
                "end": seg.end,
                "text": seg.text,
                **({"speaker": seg.speaker} if seg.speaker else {}),
            }
            for seg in transcript.segments
        ]
    else:
        data["text"] = transcript.raw_text

    return json.dumps(data, indent=2, ensure_ascii=False)


def _format_srt_timestamp(seconds: float) -> str:
    """Format seconds as SRT timestamp (HH:MM:SS,mmm)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _format_vtt_timestamp(seconds: float) -> str:
    """Format seconds as VTT timestamp (HH:MM:SS.mmm)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def export_transcript(transcript_path: Path, format: str) -> tuple[str, str, str]:
    """Export a transcript file to the specified format.

    Args:
        transcript_path: Path to the markdown transcript file.
        format: Export format (md, txt, srt, vtt, json).

    Returns:
        Tuple of (content, filename, content_type).
    """
    content = transcript_path.read_text(encoding="utf-8")
    base_name = transcript_path.stem

    if format == "md":
        return content, f"{base_name}.md", "text/markdown"

    # Parse the markdown to get structured data
    transcript = ParsedTranscript.from_markdown(content)

    if format == "txt":
        return to_plain_text(transcript), f"{base_name}.txt", "text/plain"
    elif format == "srt":
        return to_srt(transcript), f"{base_name}.srt", "application/x-subrip"
    elif format == "vtt":
        return to_vtt(transcript), f"{base_name}.vtt", "text/vtt"
    elif format == "json":
        return to_json(transcript), f"{base_name}.json", "application/json"
    else:
        raise ValueError(f"Unknown format: {format}")
