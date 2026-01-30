# Feeds

## Feed List

![Feed List](../assets/images/feed-list.png)

The main feeds page lists all subscribed podcasts with episode counts and status indicators.

**Actions:**

- **Add Feed** -- enter an RSS URL or Apple Podcasts URL
- Click a feed to view its episodes

## Feed Detail

![Feed Detail](../assets/images/feed-detail.png)

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

## Episode Detail

![Episode Detail](../assets/images/episode-detail.png)

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

## Feed Deletion

1. Click "Delete Feed" on the feed detail page
2. Type "delete" in the confirmation dialog
3. Files are moved to trash (30-day recovery window)
4. Database records are deleted immediately

!!! warning
    Database records cannot be recovered from trash. Only files (audio, transcripts) are preserved for 30 days.
