# Transcript Discovery - Technical Overview

## Overview

cast2md uses a **transcript-first workflow** that prioritizes external transcript sources before falling back to Whisper transcription. This minimizes storage requirements (no audio download needed) and processing time.

**Priority Order:**
1. **Podcast 2.0** - Publisher-provided transcripts via RSS `<podcast:transcript>` tags (authoritative)
2. **Pocket Casts** - Auto-generated transcripts from Pocket Casts API (fallback)
3. **Whisper** - Self-transcription after audio download (last resort)

---

## Phase 1: Feed Discovery (`feed/discovery.py`)

When a feed is added or refreshed via `discover_new_episodes()`:

### 1.1 RSS Parsing
```
RSS Feed → parse_feed() → ParsedEpisode objects
```

The parser (`feed/parser.py`) extracts Podcast 2.0 transcript info from each episode:
- `transcript_url` - URL from `<podcast:transcript>` tag
- `transcript_type` - MIME type (e.g., `text/vtt`, `application/srt`)

### 1.2 Episode Creation

New episodes are created in the database with:
```python
episode = episode_repo.create(
    ...
    transcript_url=ep.transcript_url,      # From RSS (Podcast 2.0)
    transcript_type=ep.transcript_type,
)
```

### 1.3 Pocket Casts Upfront Check

**After** creating episodes, the system checks Pocket Casts for episodes that **don't have** Podcast 2.0 transcripts:

```python
def _discover_pocketcasts_transcripts(feed, episodes, episode_repo, feed_repo):
    # Filter to episodes without Podcast 2.0 transcript URLs
    episodes_needing_check = [ep for ep in episodes if not ep.transcript_url]

    if not episodes_needing_check:
        return 0  # All have Podcast 2.0, skip Pocket Casts
```

**Pocket Casts API Flow:**

1. **Search for show** (`POST podcast-api.pocketcasts.com/discover/search`)
   - Search by feed title
   - Match result by author name (fuzzy matching)
   - Cache `pocketcasts_uuid` on feed for future lookups

2. **Get episodes** (`GET podcast-api.pocketcasts.com/mobile/show_notes/full/{uuid}`)
   - Returns JSON with all episodes
   - Each episode may have `pocket_casts_transcripts[]` array with VTT URLs

3. **Match episodes**
   - Title similarity (normalized, handles episode number prefixes)
   - Published date within 24 hours
   - If match found with transcript URL → store in `pocketcasts_transcript_url`

```python
for episode in episodes_needing_check:
    for pc_ep in pc_episodes:
        if _titles_similar(episode.title, pc_ep.title):
            if _dates_within_24h(episode.published_at, pc_ep.published):
                if pc_ep.transcript_url:
                    episode_repo.update_pocketcasts_transcript_url(
                        episode.id, pc_ep.transcript_url
                    )
```

---

## Phase 2: Transcript Download (`worker/manager.py`)

When a `TRANSCRIPT_DOWNLOAD` job runs:

### 2.1 Provider Chain (`transcription/providers/__init__.py`)

```python
_providers = [
    Podcast20Provider(),    # Check transcript_url first
    PocketCastsProvider(),  # Fallback to pocketcasts_transcript_url
]

def try_fetch_transcript(episode, feed):
    for provider in _providers:
        if provider.can_provide(episode, feed):
            result = provider.fetch(episode, feed)
            if result:
                return result
    return None
```

### 2.2 Podcast20Provider (`transcription/providers/podcast20.py`)

```python
def can_provide(self, episode, feed):
    return bool(episode.transcript_url)

def fetch(self, episode, feed):
    # Download from episode.transcript_url
    # Parse based on MIME type (VTT, SRT, JSON, text)
    # Convert to markdown
    return TranscriptResult(content=markdown, source="podcast2.0:vtt")
```

### 2.3 PocketCastsProvider (`transcription/providers/pocketcasts.py`)

```python
def can_provide(self, episode, feed):
    return True  # Always try as fallback

def fetch(self, episode, feed):
    # If episode.pocketcasts_transcript_url exists (from upfront discovery)
    #   → Download directly
    # Otherwise:
    #   → Search Pocket Casts API (slower path)
    #   → Match episode
    #   → Download VTT transcript
    return TranscriptResult(content=markdown, source="pocketcasts")
```

---

## Data Model

**Episode fields for transcript discovery:**

| Field | Source | Description |
|-------|--------|-------------|
| `transcript_url` | RSS parsing | Podcast 2.0 `<podcast:transcript>` URL |
| `transcript_type` | RSS parsing | MIME type of Podcast 2.0 transcript |
| `pocketcasts_transcript_url` | Upfront discovery | Pocket Casts VTT URL (discovered when feed added) |
| `transcript_source` | After download | Final source used: `whisper`, `podcast2.0:vtt`, `pocketcasts` |
| `transcript_path` | After download | Local path to saved markdown transcript |

**Feed fields:**

| Field | Description |
|-------|-------------|
| `pocketcasts_uuid` | Cached Pocket Casts show UUID (avoids repeated searches) |

---

## UI Integration

The feed detail page shows transcript availability based on these fields:

```
Transcripts: [Podcast 2.0] 150  [Pocket Casts] 23  [Whisper only] 50
```

Action buttons are conditional:
- **"Get Transcript"** - Only shown if `transcript_url` OR `pocketcasts_transcript_url` exists
- **"Download Audio"** - Shown for episodes with neither (Whisper required)

---

## Episode States for Transcript Discovery

When external transcripts are not immediately available, episodes track their transcript discovery status:

### Status Flow

```
pending → [TRANSCRIPT_DOWNLOAD job] → completed (transcript found)
                                    → transcript_pending (403, will retry)
                                    → transcript_unavailable (no transcript after retries)
                                    → pending (no external URL, needs audio download)
```

### States

| Status | Description | UI Badge Color | Action Button |
|--------|-------------|----------------|---------------|
| `pending` | Never checked or no external URL | Gray | "Get Transcript" or "Download Audio" |
| `queued` | Job is processing | Blue | Disabled "..." |
| `transcript_pending` | Got 403, will retry in 24h | Yellow | "Download Audio" |
| `transcript_unavailable` | No transcript after retries | Gray | "Download Audio" |
| `completed` | Has transcript | Green | "View" |

### 403 Handling (Pocket Casts)

Pocket Casts sometimes returns transcript URLs before the files exist on S3. The system handles this with age-based retry logic:

1. **New episodes (< 7 days old)**:
   - Status: `transcript_pending`
   - Retry: Every 24 hours
   - Rationale: Transcript may be generated soon

2. **Old episodes (>= 7 days old)**:
   - Status: `transcript_unavailable`
   - No retry: Transcript won't appear
   - User can manually download audio for Whisper

### Scheduler

The `retry_pending_transcripts()` job runs hourly to:
1. Find episodes with `transcript_pending` status and `next_transcript_retry_at <= now`
2. Re-queue `TRANSCRIPT_DOWNLOAD` jobs for eligible episodes
3. Transition episodes >= 7 days old to `transcript_unavailable`

### Database Fields

| Field | Description |
|-------|-------------|
| `transcript_checked_at` | When last transcript download was attempted |
| `next_transcript_retry_at` | When to retry (for `transcript_pending`) |
| `transcript_failure_reason` | Error type: `forbidden`, `not_found`, `request_error` |

---

## Known Limitations

1. **Pocket Casts search matching** - Relies on author name matching which may fail for some podcasts (e.g., Huberman Lab shows "Scicomm Media" as author)

2. **Rate limiting** - Pocket Casts API calls are rate-limited to 500ms between requests

3. **Episode matching** - Title normalization may not handle all edge cases (special characters, truncation)

4. **Database concurrency** - With many concurrent transcript download workers, SQLite may experience lock contention. Default is 4 workers; reduce if you see "database is locked" errors.
