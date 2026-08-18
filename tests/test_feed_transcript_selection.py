"""Choosing which podcast:transcript element to fetch.

A feed may declare the same episode's transcript in several formats. The
preference in extract_transcript_url ranks them, but it only sees a full list
when the elements are read from the raw XML — feedparser keeps just the last
one per item.
"""

from cast2md.feed.parser import extract_transcripts_from_xml, parse_feed

FEED_WITH_TWO_FORMATS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Test Show</title>
    <item>
      <title>Episode 1</title>
      <guid isPermaLink="false">episode-1</guid>
      <enclosure url="https://example.org/1.mp3" type="audio/mpeg" length="1"/>
      <podcast:transcript url="https://example.org/1.vtt" type="text/vtt"/>
      <podcast:transcript url="https://example.org/1.srt" type="application/x-subrip"/>
    </item>
  </channel>
</rss>
"""

FEED_WITHOUT_GUID = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Test Show</title>
    <item>
      <title>Episode 1</title>
      <enclosure url="https://example.org/1.mp3" type="audio/mpeg" length="1"/>
      <podcast:transcript url="https://example.org/1.srt" type="application/x-subrip"/>
      <podcast:transcript url="https://example.org/1.vtt" type="text/vtt"/>
    </item>
  </channel>
</rss>
"""


def test_raw_xml_yields_every_transcript_element():
    by_key = extract_transcripts_from_xml(FEED_WITH_TWO_FORMATS)

    assert by_key["episode-1"] == [
        {"url": "https://example.org/1.vtt", "type": "text/vtt"},
        {"url": "https://example.org/1.srt", "type": "application/x-subrip"},
    ]


def test_vtt_wins_over_srt_listed_later():
    feed = parse_feed(FEED_WITH_TWO_FORMATS)

    assert feed.episodes[0].transcript_url == "https://example.org/1.vtt"
    assert feed.episodes[0].transcript_type == "text/vtt"


def test_vtt_wins_when_listed_last():
    feed = parse_feed(FEED_WITHOUT_GUID)

    assert feed.episodes[0].transcript_url == "https://example.org/1.vtt"


def test_items_without_a_guid_key_on_the_enclosure_url():
    by_key = extract_transcripts_from_xml(FEED_WITHOUT_GUID)

    assert "https://example.org/1.mp3" in by_key


def test_malformed_xml_leaves_the_feedparser_value_in_place():
    assert extract_transcripts_from_xml("<rss><channel><item>") == {}


def test_feed_without_transcripts_parses_to_none():
    feed = parse_feed(
        """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>T</title><item>
<title>E</title><guid>g</guid>
<enclosure url="https://example.org/1.mp3" type="audio/mpeg" length="1"/>
</item></channel></rss>"""
    )

    assert feed.episodes[0].transcript_url is None
