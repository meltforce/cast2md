# cast2md

Podcast transcription service -- download episodes via RSS and transcribe with Whisper. Automatically fetches publisher-provided transcripts (Podcasting 2.0) or Pocket Casts auto-generated transcripts before falling back to local transcription.

> **Note**: This is a personal project under active development. I'm sharing it in case others find it useful, but I'm not currently providing support or reviewing pull requests.

<!-- Screenshot placeholder -->

## Features

- **Transcript-first workflow** -- fetches external transcripts from Podcasting 2.0 tags and Pocket Casts before downloading audio
- **Whisper transcription** -- local transcription with faster-whisper or mlx-whisper (CPU, CUDA, Apple Silicon)
- **Distributed transcription** -- use remote machines (M4 Macs, GPU PCs) or RunPod GPU pods to transcribe in parallel
- **Hybrid search** -- full-text and semantic search across episode metadata and transcript content (pgvector)
- **Web UI, CLI, REST API** -- manage feeds, view episodes, search transcripts, monitor processing
- **MCP server** -- Claude integration via Model Context Protocol

## Quick Start

```bash
git clone https://github.com/meltforce/cast2md.git
cd cast2md
cp .env.example .env
# Edit .env -- set POSTGRES_PASSWORD at minimum
docker compose up -d
```

Open `http://localhost:8000` to access the web UI.

## Documentation

Full documentation is available at **[cast2md.meltforce.org](https://cast2md.meltforce.org)**.

| Section | Description |
|---------|-------------|
| [Getting Started](https://cast2md.meltforce.org/getting-started/) | Architecture and key concepts |
| [Installation](https://cast2md.meltforce.org/installation/) | Docker, manual install, transcriber nodes |
| [Configuration](https://cast2md.meltforce.org/configuration/) | Environment variables, Whisper models |
| [Usage](https://cast2md.meltforce.org/usage/) | Web UI, CLI, REST API, MCP server |
| [Distributed Transcription](https://cast2md.meltforce.org/distributed/) | Multi-machine setup, RunPod GPU workers |
| [Deployment](https://cast2md.meltforce.org/deployment/) | Production deployment, server sizing |

## License

MIT
