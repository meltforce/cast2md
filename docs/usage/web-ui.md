# Web UI

The cast2md web interface is accessible at `http://localhost:8000` (or your configured host/port).

See [Screenshots](../getting-started/screenshots.md) for a visual tour of the interface.

## Pages

### Feeds

The main feeds page lists all subscribed podcasts with episode counts and status indicators.

**Actions:**

- **Add Feed** -- enter an RSS URL or Apple Podcasts URL
- Click a feed to view its episodes

### Feed Detail

Shows all episodes for a feed with status badges and action buttons.

**Episode Actions:**

| Episode Status | Button | Action |
|----------------|--------|--------|
| `new` | "Get Transcript" | Queues transcript download from external sources |
| `awaiting_transcript` | "Download Audio" | Queues audio download |
| `needs_audio` | "Download Audio" | Queues audio download |
| `audio_ready` | "Transcribe" | Queues Whisper transcription |
| `failed` | "Retry" | Queues download retry |
| `completed` | *(link)* | Opens episode detail |

**Batch Operations:**

- **Get All Transcripts** -- queues transcript download for all new episodes

**Real-time Updates:**

The page polls for status updates every 2 seconds while jobs are in progress. Status badges and buttons update automatically.

### Episode Detail

Shows full episode information with transcript viewer and manual action buttons.

**Available Actions (by status):**

| Status | Actions |
|--------|---------|
| `new` | "Try Transcript Download", "Download Audio" |
| `awaiting_transcript` | "Download Audio" |
| `needs_audio` | "Download Audio" |
| `audio_ready` | "Queue Transcription" |
| `completed` | "Delete Audio" (if audio exists) |
| `failed` | "Retry" |

**Transcript Viewer:**

- Displays transcript with timestamps
- Timestamps are clickable (if audio player is available)
- Shows transcript source (Whisper, Podcast 2.0, Pocket Casts) and model used

### Search

Unified search across episode metadata and transcript content.

**Search Modes:**

| Mode | Description |
|------|-------------|
| **Hybrid** (default) | Combines keyword and semantic search |
| **Keyword** | PostgreSQL full-text search only |
| **Semantic** | Vector similarity search only |

**Result Types:**

| Badge | Description |
|-------|-------------|
| "title" | Matched episode title or description |
| "keyword" | Matched transcript text via full-text search |
| "semantic" | Matched transcript meaning via embeddings |
| "both" | Matched by both keyword and semantic search |

Transcript results include timestamps and link directly to the relevant position in the episode.

### Status

Admin page showing system health and processing state.

**Sections:**

- **System Health** -- server info, uptime, database status
- **Workers** -- local download and transcription worker status
- **Remote Transcriber Nodes** -- connected nodes with status, current job, and last heartbeat
- **Processing Queue** -- active and queued jobs
- **Episode Counts** -- breakdown by state

### Settings

Configuration page for server settings.

**Sections:**

- **Transcription** -- Whisper model, device, compute type
- **Downloads** -- concurrent downloads, timeouts
- **Transcript Discovery** -- retry settings
- **Distributed Transcription** -- enable/disable, node management
- **Transcriber Nodes** -- list, add, test, and delete nodes
- **RunPod** -- GPU worker configuration and management
- **Notifications** -- ntfy integration

### Queue

Detailed view of the job processing queue with filtering by status.

**Stats Grid:**

- Queued, running, completed, and failed job counts

**Job List:**

- Job type, episode, status, worker assignment, timestamps
- Filter by status dropdown

## Feed Deletion

1. Click "Delete Feed" on the feed detail page
2. Type "delete" in the confirmation dialog
3. Files are moved to trash (30-day recovery window)
4. Database records are deleted immediately

!!! warning
    Database records cannot be recovered from trash. Only files (audio, transcripts) are preserved for 30 days.
