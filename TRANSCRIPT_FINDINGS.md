# Podcast Transcript Download - Findings

## Overview

A unified workflow for downloading podcast transcripts, starting from an Apple Podcasts URL.

**Priority order:**
1. **Publisher transcripts** (from RSS `<podcast:transcript>` tags) - best quality, often has speaker IDs
2. **Pocket Casts generated** (fallback) - VTT only, no speaker IDs, but widely available

---

## Unified Workflow

```
┌─────────────────────────────────────────┐
│ INPUT: Apple Podcasts URL               │
│ podcasts.apple.com/.../id{iTunes_ID}    │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│ 1. iTunes Lookup API                    │
│    GET itunes.apple.com/lookup?id={id}  │
│    → feedUrl, collectionName, artistName│
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│ 2. Fetch RSS Feed                       │
│    GET {feedUrl}                        │
│    → Parse <podcast:transcript> tags    │
└─────────────────────────────────────────┘
                    │
          ┌────────┴────────┐
          ▼                 ▼
    [Found]            [Not found]
       │                    │
       ▼                    ▼
   RETURN              ┌─────────────────────────────┐
   transcript          │ 3. Search Pocket Casts      │
                       │    POST discover/search     │
                       │    → match by author        │
                       │    → get podcast UUID       │
                       └─────────────────────────────┘
                                    │
                                    ▼
                       ┌─────────────────────────────┐
                       │ 4. Fetch Pocket Casts       │
                       │    show_notes endpoint      │
                       │    → pocket_casts_transcripts│
                       └─────────────────────────────┘
                                    │
                          ┌────────┴────────┐
                          ▼                 ▼
                    [Found]            [Not found]
                       │                    │
                       ▼                    ▼
                   RETURN              RETURN
                   transcript          "not available"
```

---

## Machine-Readable Workflow (for LLM agents)

```yaml
workflow:
  name: podcast_transcript_download
  input:
    type: url
    pattern: "podcasts.apple.com/.*/id(\\d+)"
    extract: itunes_id

  steps:
    - id: itunes_lookup
      description: Get podcast metadata and RSS feed URL
      request:
        method: GET
        url: "https://itunes.apple.com/lookup?id={itunes_id}"
      response:
        extract:
          feed_url: ".results[0].feedUrl"
          title: ".results[0].collectionName"
          author: ".results[0].artistName"
      on_error: return "podcast not found"

    - id: fetch_rss
      description: Fetch RSS feed and check for publisher transcripts
      request:
        method: GET
        url: "{feed_url}"
      response:
        parse: xml
        find_all: "item"
        for_each_item:
          extract:
            episode_title: "title"
            audio_url: "enclosure@url"
            transcript_url: "podcast:transcript[@type='application/json']@url"
            transcript_url_fallback: "podcast:transcript[@type='text/vtt']@url"
      on_success_with_transcript: return transcript_url
      on_no_transcript: continue

    - id: search_pocketcasts
      description: Search Pocket Casts by podcast name
      request:
        method: POST
        url: "https://podcast-api.pocketcasts.com/discover/search"
        headers:
          Content-Type: application/json
        body:
          term: "{title}"
      response:
        extract:
          matches: "[*]"
        filter: ".author == {author}"
        select_first:
          podcast_uuid: ".uuid"
      on_no_match: return "not available"

    - id: fetch_pocketcasts_transcripts
      description: Get transcript URLs from Pocket Casts
      request:
        method: GET
        url: "https://podcast-api.pocketcasts.com/mobile/show_notes/full/{podcast_uuid}"
      response:
        extract:
          episodes: ".podcast.episodes[*]"
        for_each_episode:
          extract:
            episode_uuid: ".uuid"
            transcript_url: ".pocket_casts_transcripts[0].url"
      on_success_with_transcript: return transcript_url
      on_no_transcript: return "not available"

  outputs:
    success:
      transcript_url: string
      format: "application/json | text/vtt"
      source: "publisher | pocketcasts"
    failure:
      status: "not available"
      reason: string
```

---

## API Reference

### iTunes Lookup API

```bash
curl -s "https://itunes.apple.com/lookup?id=1400828889"
```

**Response:**
```json
{
  "resultCount": 1,
  "results": [{
    "collectionName": "The Peter Attia Drive",
    "artistName": "Peter Attia, MD",
    "feedUrl": "https://peterattiadrive.libsyn.com/rss"
  }]
}
```

### RSS Feed - Transcript Tags

```xml
<item>
  <title>Episode Title</title>
  <enclosure url="https://example.com/episode.mp3" type="audio/mpeg"/>
  <podcast:transcript url="https://example.com/transcript.json" type="application/json"/>
  <podcast:transcript url="https://example.com/transcript.vtt" type="text/vtt"/>
</item>
```

**Priority:** Prefer `application/json` (often has speaker IDs) over `text/vtt`.

### Pocket Casts Search API

```bash
curl -s "https://podcast-api.pocketcasts.com/discover/search" \
  -X POST -H "Content-Type: application/json" \
  -d '{"term":"The Peter Attia Drive"}'
```

**Response:**
```json
[
  {
    "uuid": "dc8b3d00-56bc-0136-fa7c-0fe84b59566d",
    "title": "The Peter Attia Drive",
    "author": "Peter Attia, MD",
    "slug": "the-peter-attia-drive"
  }
]
```

**Verification:** Match `author` field against iTunes `artistName` to ensure correct podcast.

### Pocket Casts Show Notes API

```bash
curl -s "https://podcast-api.pocketcasts.com/mobile/show_notes/full/{podcast_uuid}"
```

**Response:**
```json
{
  "podcast": {
    "uuid": "dc8b3d00-56bc-0136-fa7c-0fe84b59566d",
    "episodes": [{
      "uuid": "7d2fa86f-03e2-44a0-befa-4e711b844805",
      "title": "Episode Title",
      "transcripts": [],
      "pocket_casts_transcripts": [{
        "url": "https://shownotes.pocketcasts.com/generated_transcripts/.../....vtt",
        "type": "text/vtt"
      }]
    }]
  }
}
```

**Fields:**
- `transcripts[]` - Publisher-provided (from RSS feed)
- `pocket_casts_transcripts[]` - Pocket Casts auto-generated

---

## Complete Example Script

```bash
#!/bin/bash
# Unified podcast transcript download

APPLE_URL="$1"
EPISODE_INDEX="${2:-0}"  # Default to first episode

# Step 1: Extract iTunes ID and get metadata
ITUNES_ID=$(echo "$APPLE_URL" | sed -n 's|.*/id\([0-9]*\).*|\1|p')
ITUNES_DATA=$(curl -s "https://itunes.apple.com/lookup?id=${ITUNES_ID}")

FEED_URL=$(echo "$ITUNES_DATA" | jq -r '.results[0].feedUrl')
TITLE=$(echo "$ITUNES_DATA" | jq -r '.results[0].collectionName')
AUTHOR=$(echo "$ITUNES_DATA" | jq -r '.results[0].artistName')

echo "Podcast: $TITLE by $AUTHOR"
echo "RSS Feed: $FEED_URL"

# Step 2: Check RSS feed for publisher transcripts
RSS=$(curl -s "$FEED_URL")
TRANSCRIPT_URL=$(echo "$RSS" | grep -o '<podcast:transcript[^>]*type="application/json"[^>]*>' | \
  sed -n "$((EPISODE_INDEX+1))p" | sed 's/.*url="//;s/".*//')

if [ -n "$TRANSCRIPT_URL" ]; then
  echo "Found publisher transcript: $TRANSCRIPT_URL"
  curl -s "$TRANSCRIPT_URL" -o transcript.json
  echo "Downloaded: transcript.json (source: publisher)"
  exit 0
fi

echo "No publisher transcript found, checking Pocket Casts..."

# Step 3: Search Pocket Casts
PC_RESULTS=$(curl -s "https://podcast-api.pocketcasts.com/discover/search" \
  -X POST -H "Content-Type: application/json" \
  -d "{\"term\":\"$TITLE\"}")

PODCAST_UUID=$(echo "$PC_RESULTS" | jq -r --arg author "$AUTHOR" \
  '.[] | select(.author == $author) | .uuid' | head -1)

if [ -z "$PODCAST_UUID" ] || [ "$PODCAST_UUID" = "null" ]; then
  echo "Podcast not found in Pocket Casts"
  exit 1
fi

echo "Pocket Casts UUID: $PODCAST_UUID"

# Step 4: Get Pocket Casts transcripts
SHOW_NOTES=$(curl -s "https://podcast-api.pocketcasts.com/mobile/show_notes/full/${PODCAST_UUID}")

TRANSCRIPT_URL=$(echo "$SHOW_NOTES" | jq -r \
  ".podcast.episodes[$EPISODE_INDEX].pocket_casts_transcripts[0].url // empty")

if [ -n "$TRANSCRIPT_URL" ]; then
  echo "Found Pocket Casts transcript: $TRANSCRIPT_URL"
  curl -s "$TRANSCRIPT_URL" -o transcript.vtt
  echo "Downloaded: transcript.vtt (source: pocketcasts)"
  exit 0
fi

echo "No transcript available"
exit 1
```

**Usage:**
```bash
./get_transcript.sh "https://podcasts.apple.com/us/podcast/the-peter-attia-drive/id1400828889"
./get_transcript.sh "https://podcasts.apple.com/us/podcast/the-peter-attia-drive/id1400828889" 5  # 6th episode
```

---

## Transcript Sources Summary

| Source | Auth | Speaker IDs | Formats | Availability |
|--------|------|-------------|---------|--------------|
| **Publisher** | No | Often yes | JSON, VTT, SRT | If host supports Podcast 2.0 |
| **Pocket Casts** | No | No | VTT only | Most podcasts |
| **Apple** | Yes (private) | No | TTML | macOS 15.5+ only via [third-party tool](https://github.com/dado3212/apple-podcast-transcript-downloader) |

---

## Error Handling

| Scenario | Response | Action |
|----------|----------|--------|
| iTunes ID not found | `resultCount: 0` | Return "podcast not found" |
| No RSS feed URL | `feedUrl: null` | Return "no RSS feed" |
| No `<podcast:transcript>` in RSS | Empty grep result | Continue to Pocket Casts |
| Podcast not in Pocket Casts | Empty search results | Return "not available" |
| Pocket Casts transcript 403 | HTTP 403 | Transcript not yet generated |
| Empty `pocket_casts_transcripts[]` | No transcript URL | Return "not available" |

---

## Key Endpoints Reference

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `itunes.apple.com/lookup?id={id}` | GET | Podcast metadata + RSS feed URL |
| `{feedUrl}` | GET | RSS feed with `<podcast:transcript>` tags |
| `podcast-api.pocketcasts.com/discover/search` | POST | Search podcasts by name |
| `podcast-api.pocketcasts.com/mobile/show_notes/full/{uuid}` | GET | Episode data + transcript URLs |
| `shownotes.pocketcasts.com/generated_transcripts/{p_uuid}/{e_uuid}.vtt` | GET | Pocket Casts generated transcript |

All endpoints are **public** - no authentication required.

---

## Date

Investigation conducted: 2026-01-21
