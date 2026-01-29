# Episode States

Each episode in cast2md progresses through a lifecycle of states as it moves from discovery to having a completed transcript.

## State Overview

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

---

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

---

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

- `completed` -- external transcript found and downloaded
- `awaiting_transcript` -- provider returned temporary error (e.g., 403), will retry
- `needs_audio` -- no external transcript available
- `downloading` -- user manually queued audio download

### AWAITING_TRANSCRIPT

Waiting for external transcript to become available. Common when Pocket Casts hasn't generated a transcript yet for recent episodes.

**Entry conditions:**

- External provider returned a temporary error (403, 429, etc.)
- Episode is less than `transcript_retry_days` old (default: 14 days)

**What happens:**

- System schedules automatic retry (daily)
- Retry jobs run hourly to check due episodes

**Transitions to:**

- `completed` -- external transcript found on retry
- `needs_audio` -- episode aged out without finding transcript
- `downloading` -- user manually queued audio download

### NEEDS_AUDIO

No external transcript is available. Audio must be downloaded for Whisper transcription.

**Entry conditions:**

- Episode older than `transcript_retry_days` with no external transcript URL
- External providers confirmed no transcript exists

**What happens:**

- Episode waits for user action
- Displayed with "Download Audio" button in UI

**Transitions to:**

- `downloading` -- user clicked "Download Audio"

### DOWNLOADING

Audio file is being downloaded from the podcast's server.

**Entry conditions:**

- User queued download manually
- Batch download operation started

**What happens:**

- Worker downloads audio file to storage
- Progress shown in status page

**Transitions to:**

- `audio_ready` -- download completed successfully
- `failed` -- download failed (network error, 404, etc.)

### AUDIO_READY

Audio file downloaded and ready for transcription.

**Entry conditions:**

- Audio download completed successfully

**What happens:**

- System automatically queues transcription job
- Episode waits in transcription queue

**Transitions to:**

- `transcribing` -- transcription job started

### TRANSCRIBING

Episode is being transcribed by Whisper.

**Entry conditions:**

- Transcription worker picked up the job

**What happens:**

- Whisper processes audio file
- Progress percentage shown in status page

**Transitions to:**

- `completed` -- transcription finished successfully
- `failed` -- transcription failed (Whisper error, out of memory, etc.)

### COMPLETED

Episode has a transcript available.

**Entry conditions:**

- External transcript downloaded successfully
- Whisper transcription completed successfully

**What happens:**

- Transcript viewable in episode detail page
- Searchable via full-text and semantic search
- Exportable in multiple formats (MD, TXT, SRT, VTT, JSON)

**Transitions to:**

- `transcribing` -- user requested re-transcription with newer Whisper model

### FAILED

Processing failed at some stage.

**Entry conditions:**

- Download failed (network error, file not found)
- Transcription failed (Whisper error)

**What happens:**

- Error message stored and displayed
- "Retry" button available in UI

**Transitions to:**

- `downloading` -- user clicked "Retry"
- `new` -- episode manually reset

---

## Configuration

Settings that affect state transitions:

| Setting | Default | Description |
|---------|---------|-------------|
| `transcript_unavailable_age_days` | 14 | Episodes older than this without external URLs are marked `needs_audio` immediately |
| `transcript_retry_days` | 14 | How long to retry external transcript downloads before giving up |

---

## Common Workflows

### Automatic Processing (Default)

1. Feed refresh discovers new episode -> `new`
2. Transcript download job checks external providers
3. If found -> `completed`
4. If not found and episode old -> `needs_audio`
5. If temporary error -> `awaiting_transcript` (retries daily)

### Manual Audio Download

1. User clicks "Download Audio" on episode
2. Episode -> `downloading`
3. Download completes -> `audio_ready`
4. Transcription starts -> `transcribing`
5. Transcription completes -> `completed`

### Batch Processing

1. User clicks "Get All Transcripts" on feed page
2. All `new` episodes queued for transcript download
3. Episodes without external transcripts progress through `downloading` -> `audio_ready` -> `transcribing` -> `completed`

### Re-transcription

1. User triggers re-transcription on completed episode (newer Whisper model available)
2. Episode -> `transcribing`
3. New transcript replaces old -> `completed`

---

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
