"""Speaker attribution across parsing, rendering, and export.

Publisher transcripts carry speaker names in two of the three formats they
ship: VTT voice spans (`<v Chris>`) and a `speaker` field in the JSON variant.
SRT has no construct for it. These tests cover the path from those sources to
the stored markdown and back out through the export formats.
"""

import json

from cast2md.export.formats import ParsedTranscript, to_json, to_plain_text, to_srt, to_vtt
from cast2md.search.parser import (
    TranscriptSegment as SearchSegment,
)
from cast2md.search.parser import (
    format_segments,
    merge_word_level_segments,
    parse_transcript_segments,
)
from cast2md.transcription.formats import (
    convert_to_markdown,
    parse_podcasting_json,
    parse_vtt,
)

VTT_WITH_SPEAKERS = """WEBVTT

00:00:11.488 --> 00:00:16.033
<v Chris>Hello, friends, and welcome back.

00:00:16.033 --> 00:00:16.945
<v Wes>My name is Wes.

00:00:17.345 --> 00:00:18.303
And an unattributed line.
"""


def test_parse_vtt_reads_voice_spans():
    segments = parse_vtt(VTT_WITH_SPEAKERS)

    assert [s.speaker for s in segments] == ["Chris", "Wes", None]
    assert segments[0].text == "Hello, friends, and welcome back."


def test_parse_vtt_handles_voice_span_classes():
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v.loud Brent>Loud line.\n"

    segments = parse_vtt(vtt)

    assert segments[0].speaker == "Brent"
    assert segments[0].text == "Loud line."


def test_parse_json_reads_speaker_field():
    content = json.dumps(
        [
            {"start": 0.0, "end": 2.0, "text": "First.", "speaker": "Chris"},
            {"start": 2.0, "end": 4.0, "text": "Second."},
        ]
    )

    segments = parse_podcasting_json(content)

    assert [s.speaker for s in segments] == ["Chris", None]


def test_markdown_carries_the_speaker_prefix():
    markdown, format_id = convert_to_markdown(VTT_WITH_SPEAKERS, "text/vtt", title="Episode 1")

    assert format_id == "vtt"
    assert "**[00:11]** **Chris:** Hello, friends, and welcome back." in markdown
    assert "**[00:16]** **Wes:** My name is Wes." in markdown
    assert "**[00:17]** And an unattributed line." in markdown


def test_markdown_round_trip_separates_speaker_from_text():
    markdown, _ = convert_to_markdown(VTT_WITH_SPEAKERS, "text/vtt", title="Episode 1")

    segments = parse_transcript_segments(markdown)

    assert [s.speaker for s in segments] == ["Chris", "Wes", None]
    assert segments[0].text == "Hello, friends, and welcome back."
    assert not any(s.text.startswith("**") for s in segments)


def test_transcript_without_speakers_parses_unchanged():
    markdown = "# Title\n\n**[00:00]** Hello world.\n\n**[00:05]** Second line.\n"

    segments = parse_transcript_segments(markdown)

    assert [s.speaker for s in segments] == [None, None]
    assert segments[0].text == "Hello world."


def test_merge_stops_at_a_speaker_change():
    segments = [
        SearchSegment(text="one", start=0.0, end=0.5, speaker="Chris"),
        SearchSegment(text="two", start=0.5, end=1.0, speaker="Chris"),
        SearchSegment(text="three", start=1.0, end=1.5, speaker="Wes"),
    ]

    merged = merge_word_level_segments(segments)

    assert [(m.speaker, m.text) for m in merged] == [("Chris", "one two"), ("Wes", "three")]


def test_merge_keeps_merging_within_one_speaker():
    segments = [
        SearchSegment(text="one", start=0.0, end=0.5, speaker="Chris"),
        SearchSegment(text="two", start=0.5, end=1.0, speaker="Chris"),
    ]

    merged = merge_word_level_segments(segments)

    assert len(merged) == 1
    assert merged[0].speaker == "Chris"


def test_format_segments_labels_the_speaker():
    segments = [
        SearchSegment(text="Hello.", start=11.0, end=12.0, speaker="Chris"),
        SearchSegment(text="Hi.", start=12.0, end=13.0),
    ]

    assert format_segments(segments) == "[00:11] Chris: Hello.\n[00:12] Hi."


MARKDOWN_WITH_SPEAKERS = """# Episode 1

*Language: en (99.0%)*

**[00:00]** **Chris:** Hello there.

**[00:05]** **Wes:** Hi Chris.

**[00:10]** No speaker here.
"""


def test_export_parses_the_speaker_prefix():
    transcript = ParsedTranscript.from_markdown(MARKDOWN_WITH_SPEAKERS)

    assert [s.speaker for s in transcript.segments] == ["Chris", "Wes", None]
    assert transcript.segments[0].text == "Hello there."


def test_export_vtt_writes_voice_spans():
    transcript = ParsedTranscript.from_markdown(MARKDOWN_WITH_SPEAKERS)

    vtt = to_vtt(transcript)

    assert "<v Chris>Hello there." in vtt
    assert "<v Wes>Hi Chris." in vtt
    assert "\nNo speaker here." in vtt


def test_export_srt_drops_the_speaker():
    transcript = ParsedTranscript.from_markdown(MARKDOWN_WITH_SPEAKERS)

    srt = to_srt(transcript)

    assert "Hello there." in srt
    assert "<v " not in srt
    assert "Chris:" not in srt


def test_export_json_includes_the_speaker_only_when_known():
    transcript = ParsedTranscript.from_markdown(MARKDOWN_WITH_SPEAKERS)

    data = json.loads(to_json(transcript))

    assert data["segments"][0]["speaker"] == "Chris"
    assert "speaker" not in data["segments"][2]


def test_export_plain_text_starts_a_paragraph_per_speaker():
    transcript = ParsedTranscript.from_markdown(MARKDOWN_WITH_SPEAKERS)

    text = to_plain_text(transcript)

    assert "Chris: Hello there." in text
    assert "Wes: Hi Chris." in text
    assert "\nNo speaker here." in text
