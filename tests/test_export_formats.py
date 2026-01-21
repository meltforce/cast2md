
from cast2md.export.formats import ParsedTranscript, TranscriptSegment, to_srt, to_vtt, to_json, to_plain_text, export_transcript
from pathlib import Path
import json

def test_parsed_transcript_from_markdown():
    markdown = """# Test Title

*Language: en (99.0%)*

**[00:00]** Hello world.

**[00:05]** This is a test.
"""
    transcript = ParsedTranscript.from_markdown(markdown)
    assert transcript.title == "Test Title"
    # The current regex extraction for language might be simple, let's check
    assert transcript.language == "en"
    assert len(transcript.segments) == 2
    assert transcript.segments[0].start == 0.0
    assert transcript.segments[0].end == 5.0  # Inferred from next segment
    assert transcript.segments[0].text == "Hello world."
    assert transcript.segments[1].start == 5.0
    assert transcript.segments[1].end == 10.0 # Default +5s
    assert transcript.segments[1].text == "This is a test."

def test_to_srt():
    segments = [
        TranscriptSegment(start=0.0, end=5.5, text="Hello"),
        TranscriptSegment(start=5.5, end=10.0, text="World")
    ]
    transcript = ParsedTranscript(title="Test", language="en", segments=segments)
    srt = to_srt(transcript)
    
    expected = """1
00:00:00,000 --> 00:00:05,500
Hello

2
00:00:05,500 --> 00:00:10,000
World
"""
    assert srt.strip() == expected.strip()

def test_to_vtt():
    segments = [
        TranscriptSegment(start=0.0, end=5.5, text="Hello"),
    ]
    transcript = ParsedTranscript(title="Test", language="en", segments=segments)
    vtt = to_vtt(transcript)
    
    assert "WEBVTT" in vtt
    assert "00:00:00.000 --> 00:00:05.500" in vtt
    assert "Hello" in vtt

def test_to_json():
    segments = [
        TranscriptSegment(start=0.0, end=1.0, text="Hi"),
    ]
    transcript = ParsedTranscript(title="Test", language="en", segments=segments)
    json_str = to_json(transcript)
    data = json.loads(json_str)
    
    assert data["title"] == "Test"
    assert data["language"] == "en"
    assert len(data["segments"]) == 1
    assert data["segments"][0]["text"] == "Hi"

def test_export_transcript_integration(tmp_path):
    md_file = tmp_path / "test.md"
    md_content = """# Test
**[00:00]** Content
"""
    md_file.write_text(md_content)
    
    content, filename, mime = export_transcript(md_file, "srt")
    assert filename == "test.srt"
    assert mime == "application/x-subrip"
    assert "00:00:00,000 -->" in content
