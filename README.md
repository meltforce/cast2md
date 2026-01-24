# cast2md

Podcast transcription service - download episodes via RSS and transcribe with Whisper. Automatically downloads publisher-provided transcripts when available (Podcasting 2.0) or fetches auto-generated transcripts from Pocket Casts.

> **Note**: This is a personal project under active development. I'm sharing it in case others find it useful, but I'm not currently providing support or reviewing pull requests.

## Features

- **iTunes URL Support**: Add podcasts via Apple Podcasts URLs (automatically resolves to RSS)
- **RSS Feed Management**: Add podcast feeds and automatically discover new episodes
- **External Transcript Downloads**: Fetches transcripts from Podcasting 2.0 tags and Pocket Casts before falling back to Whisper
- **Whisper Transcription**: Transcribe audio using faster-whisper or mlx-whisper
- **Distributed Transcription**: Use remote machines (M4 Macs, GPU PCs) to transcribe in parallel
- **Full-Text Search**: Search across episode metadata and transcripts
- **Web Interface**: Manage feeds, view episodes, and monitor progress
- **REST API**: Full API for integration with other tools
- **MCP Server**: Claude integration via Model Context Protocol

## Installation

### Docker

```bash
git clone https://github.com/meltforce/cast2md.git
cd cast2md
docker compose up -d
```

### Manual Installation

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone https://github.com/meltforce/cast2md.git
cd cast2md
uv sync --frozen

# Configure
cp .env.example .env
# Edit .env with your settings

# Initialize and run
uv run cast2md init-db
uv run cast2md serve
```

## Configuration

Create a `.env` file:

```env
DATABASE_PATH=./data/cast2md.db
STORAGE_PATH=./data/media
TEMP_DOWNLOAD_PATH=./data/temp

# Whisper settings
WHISPER_MODEL=medium          # tiny, base, small, medium, large-v3
WHISPER_DEVICE=cpu            # cpu or cuda
WHISPER_COMPUTE_TYPE=int8     # int8, float16, float32
```

### Whisper Models

| Model | Quality | Speed (CPU) | RAM |
|-------|---------|-------------|-----|
| tiny | Basic | ~10x realtime | 1 GB |
| base | Good | ~5x realtime | 2 GB |
| small | Very good | ~2x realtime | 3 GB |
| medium | Excellent | ~1x realtime | 6 GB |
| large-v3 | Best | ~0.3x realtime | 12 GB |

## Usage

### Web Interface

Access at `http://localhost:8000`

- **Feeds**: Add and manage podcast RSS feeds
- **Episodes**: View episodes and transcription status
- **Search**: Search across titles, descriptions, and transcripts
- **Admin**: Monitor system health and processing queue

### CLI

```bash
# Add a podcast (RSS or Apple Podcasts URL)
cast2md add-feed "https://example.com/feed.xml"
cast2md add-feed "https://podcasts.apple.com/us/podcast/example/id123456"

# List and manage
cast2md list-feeds
cast2md list-episodes <feed_id>

# Process episodes
cast2md download <episode_id>
cast2md transcribe <episode_id>
cast2md process <episode_id>  # download + transcribe

# Server
cast2md serve --host 0.0.0.0 --port 8000

# Backup/restore
cast2md backup -o backup.sql
cast2md restore backup.sql
```

### MCP Server (Claude Integration)

```json
{
  "mcpServers": {
    "podcasts": {
      "command": "/path/to/cast2md",
      "args": ["mcp"]
    }
  }
}
```

Available tools: `search_transcripts`, `search_episodes`, `queue_episode`, `add_feed`, `refresh_feed`

### Distributed Transcription

Run transcription workers on remote machines:

```bash
# On remote machine (Mac with MLX, GPU PC)
cast2md node register --server http://server:8000 --name "Worker Name"
cast2md node start
```

See [Distributed Transcription Setup](docs/distributed-transcription-setup.md) for details.

## API

| Endpoint | Description |
|----------|-------------|
| `GET /api/feeds` | List feeds |
| `POST /api/feeds` | Add feed |
| `GET /api/episodes/{id}` | Episode details |
| `GET /api/episodes/{id}/transcript` | Download transcript |
| `POST /api/queue/episodes/{id}/process` | Queue download |
| `POST /api/queue/episodes/{id}/transcribe` | Queue transcription |
| `GET /api/queue/status` | Queue status |

## Development

```bash
uv sync
uv run cast2md serve --reload
uv run pytest
```

## License

MIT
