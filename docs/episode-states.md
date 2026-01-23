# Episode State Machine

This document describes the lifecycle states of episodes in cast2md and how episodes transition between them.

## State Overview

Each episode progresses through a pipeline with four stages:

```
Check External → Download Audio → Transcribe → Done
```

| State | Description |
|-------|-------------|
| `new` | Just discovered, ready to process |
| `awaiting_transcript` | Checking external sources, will retry later |
| `needs_audio` | No external transcript available, audio download required |
| `downloading` | Currently downloading audio file |
| `audio_ready` | Audio downloaded, ready for Whisper transcription |
| `transcribing` | Currently being transcribed by Whisper |
| `completed` | Transcript available |
| `failed` | Processing failed (see error message) |

## State Diagram

```
                                    ┌─────────────────────────────────────────┐
                                    │                                         │
                                    ▼                                         │
┌─────────┐     ┌─────────────────────────┐     ┌─────────────┐              │
│   NEW   │────▶│   AWAITING_TRANSCRIPT   │────▶│ NEEDS_AUDIO │──────────────┤
└─────────┘     └─────────────────────────┘     └─────────────┘              │
    │                     │                            │                      │
    │                     │ (external transcript       │                      │
    │                     │  found)                    │                      │
    │                     ▼                            ▼                      │
    │              ┌───────────┐              ┌─────────────┐                │
    │              │ COMPLETED │              │ DOWNLOADING │                │
    │              └───────────┘              └─────────────┘                │
    │                    ▲                            │                      │
    │                    │                            ▼                      │
    │                    │                    ┌─────────────┐                │
    │                    │                    │ AUDIO_READY │                │
    │                    │                    └─────────────┘                │
    │                    │                            │                      │
    │                    │                            ▼                      │
    │                    │                    ┌──────────────┐     ┌────────┐
    │                    └────────────────────│ TRANSCRIBING │────▶│ FAILED │
    │                                         └──────────────┘     └────────┘
    │                                                                   │
    └───────────────────────────────────────────────────────────────────┘
                              (retry)
```

## State Details

### NEW

The initial state for all newly discovered episodes.

**Entry conditions:**
- Episode discovered during feed refresh
- Episode reset for reprocessing

**What happens:**
- System automatically queues a transcript download job
- Checks external transcript providers (Podcast 2.0, Pocket Casts)

**Transitions to:**
- `completed` - External transcript found and downloaded
- `awaiting_transcript` - Provider returned temporary error (e.g., 403), will retry
- `needs_audio` - No external transcript available
- `downloading` - User manually queued audio download

### AWAITING_TRANSCRIPT

Waiting for external transcript to become available. Common when Pocket Casts hasn't generated a transcript yet for recent episodes.

**Entry conditions:**
- External provider returned a temporary error (403, 429, etc.)
- Episode is less than 7 days old

**What happens:**
- System schedules automatic retry (daily for up to 7 days)
- Retry jobs run hourly to check due episodes

**Transitions to:**
- `completed` - External transcript found on retry
- `needs_audio` - Episode aged out (> 7 days) without finding transcript
- `downloading` - User manually queued audio download

### NEEDS_AUDIO

No external transcript is available. Audio must be downloaded for Whisper transcription.

**Entry conditions:**
- Episode is old (> 90 days) with no external transcript URL
- Episode waited 7+ days in `awaiting_transcript` without success
- External providers confirmed no transcript exists

**What happens:**
- Episode waits for user action
- Displayed with "Download Audio" button in UI

**Transitions to:**
- `downloading` - User clicked "Download Audio"

### DOWNLOADING

Audio file is being downloaded from the podcast's server.

**Entry conditions:**
- User queued download manually
- Batch download operation started

**What happens:**
- Worker downloads audio file to storage
- Progress shown in status page

**Transitions to:**
- `audio_ready` - Download completed successfully
- `failed` - Download failed (network error, 404, etc.)

### AUDIO_READY

Audio file downloaded and ready for transcription.

**Entry conditions:**
- Audio download completed successfully

**What happens:**
- System automatically queues transcription job
- Episode waits in transcription queue

**Transitions to:**
- `transcribing` - Transcription job started
- `failed` - Transcription job failed to start

### TRANSCRIBING

Episode is being transcribed by Whisper.

**Entry conditions:**
- Transcription worker picked up the job

**What happens:**
- Whisper processes audio file
- Progress percentage shown in status page
- Can take 5-30+ minutes depending on episode length and hardware

**Transitions to:**
- `completed` - Transcription finished successfully
- `failed` - Transcription failed (Whisper error, out of memory, etc.)

### COMPLETED

Episode has a transcript available.

**Entry conditions:**
- External transcript downloaded successfully
- Whisper transcription completed successfully

**What happens:**
- Transcript viewable in episode detail page
- Searchable via semantic search
- Exportable in multiple formats (MD, TXT, SRT, VTT, JSON)

**Transitions to:**
- `transcribing` - User requested re-transcription with newer Whisper model

### FAILED

Processing failed at some stage.

**Entry conditions:**
- Download failed (network error, file not found)
- Transcription failed (Whisper error)
- Any unrecoverable error

**What happens:**
- Error message stored and displayed
- "Retry" button available in UI

**Transitions to:**
- `downloading` - User clicked "Retry" (resets and tries download again)
- `new` - Episode manually reset

## Transcript Sources

When an episode reaches `completed`, the transcript source indicates how it was obtained:

| Source | Description |
|--------|-------------|
| `whisper` | Transcribed locally using Whisper |
| `podcast2.0:vtt` | Downloaded from publisher (WebVTT format) |
| `podcast2.0:srt` | Downloaded from publisher (SRT format) |
| `podcast2.0:json` | Downloaded from publisher (JSON format) |
| `podcast2.0:text` | Downloaded from publisher (plain text) |
| `pocketcasts` | Auto-generated by Pocket Casts |

## Configuration

Related settings that affect state transitions:

| Setting | Default | Description |
|---------|---------|-------------|
| `transcript_unavailable_age_days` | 90 | Episodes older than this without external URLs are marked `needs_audio` immediately |
| `transcript_retry_days` | 7 | How long to retry external transcript downloads before giving up |

## API Status Endpoint

The `/api/status` endpoint returns episode counts by state:

```json
{
  "episode_counts": {
    "new": 5,
    "awaiting_transcript": 12,
    "needs_audio": 45,
    "downloading": 0,
    "audio_ready": 2,
    "transcribing": 1,
    "completed": 1523,
    "failed": 3,
    "total": 1591
  }
}
```

## Common Workflows

### Automatic Processing (Default)

1. Feed refresh discovers new episode → `new`
2. Transcript download job checks external providers
3. If found → `completed`
4. If not found and episode old → `needs_audio`
5. If temporary error → `awaiting_transcript` (retries daily)

### Manual Audio Download

1. User clicks "Download Audio" on `new`, `awaiting_transcript`, or `needs_audio` episode
2. Episode → `downloading`
3. Download completes → `audio_ready`
4. Transcription starts → `transcribing`
5. Transcription completes → `completed`

### Batch Processing

1. User clicks "Download & Transcribe All" on feed page
2. All `new` and `needs_audio` episodes queued
3. Episodes progress through `downloading` → `audio_ready` → `transcribing` → `completed`

### Re-transcription

1. User clicks "Re-transcribe" on completed episode (when newer Whisper model available)
2. Episode → `transcribing`
3. New transcript replaces old → `completed`
