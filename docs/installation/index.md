# Installation

cast2md can be installed in several ways depending on your needs.

!!! info "Single server handles everything"
    A single cast2md server handles the complete workflow -- downloading, transcription, and search. Transcriber nodes are optional and only needed to speed up transcription for large backlogs.

## Installation Methods

| Method | Best For | Requirements |
|--------|----------|--------------|
| [Docker](docker.md) | Server: production, quick start | Docker, Docker Compose |
| [Manual Install](manual.md) | Server: development, customization | Python 3.11+, PostgreSQL |
| [Transcriber Node](node.md) | Optional remote transcription workers | Python 3.11+, network access to server |

## Quick Start (Docker)

The fastest way to get running:

```bash
git clone https://github.com/meltforce/cast2md.git
cd cast2md
cp .env.example .env
# Edit .env -- set POSTGRES_PASSWORD at minimum
docker compose up -d
```

Open `http://localhost:8000` to access the web UI.

## System Requirements

### Server

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| RAM | 2 GB | 4 GB |
| Disk | 10 GB | 30 GB |
| CPU | 2 cores | 4 cores |
| Python | 3.11+ | 3.12+ |

### Transcriber Node

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| RAM | 2 GB | 4 GB |
| Python | 3.11+ | 3.12+ |
| GPU (optional) | - | NVIDIA 8GB+ VRAM or Apple Silicon |

See [Server Sizing](../deployment/server-sizing.md) for detailed resource requirements.
