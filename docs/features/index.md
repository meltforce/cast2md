# Features

cast2md provides a comprehensive set of features for podcast transcription and search.

## Core Features

### Transcript-First Workflow

cast2md checks external sources before downloading audio:

1. **Podcasting 2.0** -- publisher-provided transcripts via RSS tags
2. **Pocket Casts** -- auto-generated transcripts
3. **Whisper** -- local transcription (fallback)

See [Transcript Sources](transcript-sources.md) for technical details.

### Hybrid Search

Search across episode metadata and transcript content using:

- **Full-text search** -- PostgreSQL tsvector keyword matching
- **Semantic search** -- sentence-transformers embeddings with pgvector
- **Reciprocal Rank Fusion** -- combines both for best relevance

See [Search](search.md) for the search architecture.

### Episode State Machine

Episodes progress through a well-defined lifecycle from discovery to completed transcript.

See [Episode States](episode-states.md) for the full state diagram and transitions.

## Additional Features

| Feature | Description |
|---------|-------------|
| **iTunes URL Support** | Add podcasts via Apple Podcasts URLs |
| **RSS Feed Management** | Automatic episode discovery and polling |
| **Distributed Transcription** | Use remote machines to transcribe in parallel |
| **RunPod GPU Workers** | On-demand GPU pods for batch processing |
| **Audio Management** | Delete audio after transcription to save space |
| **Feed Trash** | Deleted feeds are moved to trash (30-day recovery) |
| **Backup/Restore** | PostgreSQL pg_dump based backup and restore |
| **Notifications** | ntfy.sh integration for processing events |
| **MCP Server** | Claude AI integration via Model Context Protocol |
| **Re-transcription** | Re-transcribe episodes with newer Whisper models |
