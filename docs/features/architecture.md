# Architecture

## System Overview

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

## Components

- **Server** -- FastAPI application with web UI, REST API, and background workers
- **PostgreSQL** -- database with pgvector extension for semantic search
- **Workers** -- download workers and local transcription worker run in the server process
- **Remote nodes** (optional) -- additional machines for distributed transcription
- **RunPod** (optional) -- on-demand GPU pods for batch processing

## Interfaces

| Interface | Description |
|-----------|-------------|
| **Web UI** | Manage feeds, view episodes, search transcripts |
| **CLI** | Command-line tool for all operations |
| **REST API** | Full API for automation and integration |
| **MCP Server** | Claude integration via Model Context Protocol |

## Transcription Backends

| Backend | Use Case | Languages | Speed |
|---------|----------|-----------|-------|
| **Whisper** (faster-whisper) | Local CPU/GPU transcription | 99+ languages | Varies by model |
| **Whisper** (mlx-whisper) | Apple Silicon Macs | 99+ languages | Fast on M-series |
| **Parakeet** | RunPod GPU pods | 25 EU languages | ~100x realtime |
