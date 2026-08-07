# Web UI Workflow

## Feed Episode List (feed_detail.html)

The episode list uses a **transcript-first** approach with real-time status updates:

### Button Behavior

| Episode Status | Button | Action |
|----------------|--------|--------|
| `new` | "Get Transcript" | Queues `TRANSCRIPT_DOWNLOAD` job |
| `awaiting_transcript` | "Download Audio" | Queues `DOWNLOAD` job |
| `needs_audio` | "Download Audio" | Queues `DOWNLOAD` job |
| `audio_ready` | "Transcribe" | Queues `TRANSCRIBE` job (Whisper) |
| `failed` | "Retry" | Queues `DOWNLOAD` job |
| `downloading`, `transcribing` | "..." (disabled) | No action, status shown in badge |
| `completed` | (none) | Link to episode detail |

### Transcript Download Flow

1. User clicks "Get Transcript" → `POST /api/queue/episodes/{id}/transcript-download`
2. Button becomes disabled ("..."), status badge shows "queued"
3. Worker tries Podcast20Provider, then PocketCastsProvider
4. If found: episode marked `completed`, button becomes link to detail
5. If not found: episode becomes `awaiting_transcript` or `needs_audio`, button shows "Download Audio"

When no external transcript is available, the button changes to "Download Audio" which queues the full audio download + Whisper transcription pipeline.

### Real-time Status Updates

The feed page polls `/api/feeds/{id}/episodes` every 2 seconds while jobs are in progress:

- `startStatusPolling()` - Starts interval timer
- `stopStatusPolling()` - Stops when all visible episodes are in a stable state (completed/failed/new/needs_audio)
- `pollEpisodeStatus()` - Fetches current status and updates DOM
- `updateEpisodeRow()` - Updates badge, checkbox, and action button

Polling uses visible episode IDs from DOM (not template-rendered array) to handle pagination correctly.

### Batch Operations

- "Get All Transcripts" button queues all new episodes via `POST /api/queue/batch/feed/{id}/transcript-download`

## Episode Detail Page (episode_detail.html)

Shows full episode info with transcript viewer and manual action buttons:

| Status | Available Actions |
|--------|-------------------|
| `new` | "Try Transcript Download", "Download Audio" |
| `awaiting_transcript` | "Download Audio" |
| `needs_audio` | "Download Audio" |
| `audio_ready` | "Queue Transcription" |
| `completed` | "Delete Audio" (if audio exists), "Download Audio" (if deleted) |
| `failed` | "Retry" |

## Tooltips

Use one tooltip pattern: the square `.tip` CSS bubble with a `title` attribute. The attribute keeps the accessible text and `.tip::after` renders the visual treatment. Do not add another tooltip implementation.
