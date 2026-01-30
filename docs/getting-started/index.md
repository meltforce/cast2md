# Getting Started

cast2md is a podcast transcription service. Add your podcast feeds, and cast2md builds a searchable transcript library -- automatically.

## How It Works

```
New Episode Discovered
        │
        ▼
  Check External Transcripts
  (Podcast 2.0, Pocket Casts)
        │
   ┌────┴────┐
   │         │
Found    Not Found
   │         │
   ▼         ▼
 Done    Download Audio
            │
            ▼
      Transcribe (Whisper)
            │
            ▼
          Done
```

1. **Feed discovery** -- add RSS feeds, episodes are discovered automatically
2. **Transcript download** -- checks publisher transcripts and Pocket Casts first
3. **Audio fallback** -- downloads audio only when no external transcript exists
4. **Whisper transcription** -- local or distributed transcription
5. **Search & access** -- full-text and semantic search, REST API, MCP for Claude

## Transcript-First Workflow

When a new episode is discovered, cast2md doesn't immediately download audio. Instead, it first checks for existing transcripts:

1. **Podcast 2.0** -- publisher-provided transcripts via RSS `<podcast:transcript>` tags
2. **Pocket Casts** -- auto-generated transcripts from the Pocket Casts API
3. **Whisper** -- local transcription after audio download (last resort)

This saves storage and processing time -- audio is only downloaded when no external transcript is available.

!!! tip "Audio download is always available"
    You can always download audio manually for any episode, regardless of transcript availability. Use "Download Audio" on the episode detail page or the CLI/API.

## Search

cast2md includes hybrid search combining full-text and semantic search:

- **Keyword search** -- PostgreSQL full-text search for exact term matching
- **Semantic search** -- sentence-transformers embeddings with pgvector for meaning-based queries (e.g., "cold water swimming" finds episodes about "Eisbaden")
- **Hybrid mode** (default) -- combines both using Reciprocal Rank Fusion for best relevance

Search works across episode titles, descriptions, and transcript content.

## Single Server is Enough

A single cast2md server handles the complete workflow -- downloading episodes, transcribing audio, and searching transcripts. No additional setup is needed beyond the server itself.

Remote transcriber nodes and RunPod GPU workers are entirely optional. They speed up transcription for large backlogs but aren't required for normal use.

## Next Steps

- [Install cast2md](../installation/index.md)
- [Configure your environment](../configuration/index.md)
- [Learn about search and feeds](../usage/index.md)
