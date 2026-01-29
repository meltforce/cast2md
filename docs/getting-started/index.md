# Getting Started

cast2md is a podcast transcription service that downloads episodes via RSS and transcribes them using Whisper. It prioritizes external transcript sources (Podcasting 2.0, Pocket Casts) before falling back to local transcription.

## Key Concepts

### Transcript-First Workflow

When a new episode is discovered, cast2md doesn't immediately download audio. Instead, it first checks for existing transcripts:

1. **Podcast 2.0** -- publisher-provided transcripts via RSS `<podcast:transcript>` tags
2. **Pocket Casts** -- auto-generated transcripts from the Pocket Casts API
3. **Whisper** -- local transcription after audio download (last resort)

This saves storage and processing time. Audio is only downloaded when no external transcript is available.

### Architecture

```
┌─────────────────────────────────────────┐
│            cast2md Server               │
│                                         │
│  ┌──────────┐  ┌───────────────────┐    │
│  │ Web UI   │  │ REST API          │    │
│  └──────────┘  └───────────────────┘    │
│  ┌──────────┐  ┌───────────────────┐    │
│  │ Workers  │  │ PostgreSQL + pgvec│    │
│  └──────────┘  └───────────────────┘    │
└────────────────────┬────────────────────┘
                     │
        ┌────────────┼────────────┐
        │            │            │
   ┌─────────┐ ┌─────────┐ ┌─────────┐
   │ Node A  │ │ Node B  │ │ RunPod  │
   │ M4 Mac  │ │ GPU PC  │ │ A5000   │
   └─────────┘ └─────────┘ └─────────┘
         (optional remote workers)
```

**Components:**

- **Server** -- FastAPI application with web UI, REST API, and background workers
- **PostgreSQL** -- database with pgvector extension for semantic search
- **Workers** -- download workers and local transcription worker run in the server process
- **Remote nodes** (optional) -- additional machines for distributed transcription
- **RunPod** (optional) -- on-demand GPU pods for batch processing

### Episode Lifecycle

Each episode passes through several states:

| State | Description |
|-------|-------------|
| `new` | Just discovered |
| `awaiting_transcript` | Checking external sources, will retry |
| `needs_audio` | No external transcript, audio download needed |
| `downloading` | Audio being downloaded |
| `audio_ready` | Audio ready for Whisper |
| `transcribing` | Being transcribed |
| `completed` | Transcript available |
| `failed` | Processing failed |

See [Episode States](../features/episode-states.md) for the full state machine.

### Interfaces

cast2md provides multiple interfaces:

| Interface | Description |
|-----------|-------------|
| **Web UI** | Manage feeds, view episodes, search transcripts |
| **CLI** | Command-line tool for all operations |
| **REST API** | Full API for automation and integration |
| **MCP Server** | Claude integration via Model Context Protocol |

### Transcription Backends

| Backend | Use Case | Languages | Speed |
|---------|----------|-----------|-------|
| **Whisper** (faster-whisper) | Local CPU/GPU transcription | 99+ languages | Varies by model |
| **Whisper** (mlx-whisper) | Apple Silicon Macs | 99+ languages | Fast on M-series |
| **Parakeet** | RunPod GPU pods | 25 EU languages | ~100x realtime |

### Search

cast2md includes hybrid search combining:

- **Full-text search** -- PostgreSQL tsvector for keyword matching
- **Semantic search** -- sentence-transformers embeddings with pgvector for meaning-based queries
- **Reciprocal Rank Fusion** -- combines both result sets for best relevance

## Next Steps

- [Install cast2md](../installation/index.md)
- [Configure your environment](../configuration/index.md)
- [Learn the Web UI](../usage/web-ui.md)
- [Set up distributed transcription](../distributed/index.md)
