# cast2md

Podcast transcription service - download episodes via RSS and transcribe with Whisper. Automatically downloads publisher-provided transcripts when available (Podcasting 2.0) or fetches auto-generated transcripts from Pocket Casts.

> **Note**: This is a personal project under active development. I'm sharing it in case others find it useful, but I'm not currently providing support or reviewing pull requests.

## Features

- **iTunes URL Support**: Add podcasts via Apple Podcasts URLs (automatically resolves to RSS)
- **RSS Feed Management**: Add podcast feeds and automatically discover new episodes
- **Extended Metadata**: Extracts author, website link, and categories from RSS feeds
- **Custom Feed Titles**: Override RSS titles with custom names (auto-renames storage directories)
- **Automatic Downloads**: Queue and download episodes with configurable workers
- **External Transcript Downloads**: Automatically fetches transcripts from multiple sources before falling back to Whisper:
  - Podcasting 2.0 `<podcast:transcript>` tags (publisher-provided)
  - Pocket Casts auto-generated transcripts (public API, no auth required)
- **Whisper Transcription**: Transcribe audio using faster-whisper or mlx-whisper (auto-converts to mono 16kHz for optimal accuracy)
- **Re-transcription Support**: Track which model was used; re-transcribe with different model when upgrading
- **Distributed Transcription**: Use remote machines (M4 Macs, GPU PCs) to transcribe in parallel
- **Full-Text Search**: Unified search across episode metadata and transcripts with detail modal
- **Web Interface**: Simple UI to manage feeds, view episodes, and monitor progress
- **Real-time Status**: Episode status updates automatically without page reload
- **Show Notes Display**: Preview and full modal view with sanitized HTML
- **REST API**: Full API for integration with other tools
- **MCP Server**: Claude integration via Model Context Protocol for AI-powered podcast exploration
- **Background Processing**: Scheduled feed polling and queue-based transcription
- **Database Migrations**: Automatic schema migrations for seamless upgrades
- **NAS Storage**: Store transcripts and audio on network storage

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Proxmox LXC (Debian)                                   │
│  ┌─────────────────────────────────────────────────┐   │
│  │  cast2md (systemd service)                       │   │
│  │  - SQLite: /opt/cast2md/data/cast2md.db         │   │
│  │  - Config: /opt/cast2md/.env                     │   │
│  └─────────────────────────────────────────────────┘   │
│                         │                               │
│            /mnt/nas/cast2md (NFS mount)                │
└─────────────────────────────────────────────────────────┘
                          │
              ┌───────────────────────┐
              │  Synology NAS         │
              │  - audio/             │
              │  - transcripts/       │
              └───────────────────────┘
```

## Installation

### Option 1: Docker (for local testing)

```bash
# Clone the repository
git clone https://github.com/meltforce/cast2md.git
cd cast2md

# Build and run
docker compose up -d

# Check health
curl http://localhost:8000/api/health
```

### Option 2: LXC/Server Deployment

#### Prerequisites

- Debian 12+ or Ubuntu 22.04+
- Python 3.11+
- NFS mount for media storage (optional)

#### Quick Install

```bash
# Install dependencies
apt update && apt install -y python3-venv python3-pip git curl

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env

# Clone and install
git clone https://github.com/meltforce/cast2md.git /opt/cast2md
cd /opt/cast2md
uv sync --frozen

# Create data directory
mkdir -p /opt/cast2md/data

# Configure
cp .env.example .env
# Edit .env with your settings

# Initialize database
.venv/bin/python -m cast2md init-db

# Install systemd service
cp deploy/cast2md.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable cast2md
systemctl start cast2md
```

## Configuration

Create a `.env` file in the project root:

```env
# Database location (local storage recommended)
DATABASE_PATH=/opt/cast2md/data/cast2md.db

# Media storage (can be NFS mount)
STORAGE_PATH=/mnt/nas/cast2md

# Temporary download location
TEMP_DOWNLOAD_PATH=/opt/cast2md/data/temp

# Whisper settings
WHISPER_MODEL=medium          # tiny, base, small, medium, large-v3
WHISPER_DEVICE=cpu            # cpu or cuda
WHISPER_COMPUTE_TYPE=int8     # int8, float16, float32
```

### Whisper Model Selection

| Model | Quality | Speed (CPU) | RAM Required |
|-------|---------|-------------|--------------|
| tiny | Basic | ~10x realtime | 1 GB |
| base | Good | ~5x realtime | 2 GB |
| small | Very good | ~2x realtime | 3 GB |
| medium | Excellent | ~1x realtime | 6 GB |
| large-v3 | Best | ~0.3x realtime | 12 GB |

### Recommended Container Resources

| Model | CPU Cores | RAM | Swap |
|-------|-----------|-----|------|
| base | 2 | 2 GB | 1 GB |
| medium | 4 | 6 GB | 2 GB |
| large-v3 | 6 | 12 GB | 4 GB |

### Transcript Sources

cast2md uses a pluggable provider system to fetch transcripts. When adding a feed or refreshing episodes, it tries external sources before falling back to Whisper:

1. **Podcasting 2.0** (Priority 1) - Downloads from `<podcast:transcript>` RSS tags
   - Supports: VTT, SRT, JSON, plain text, HTML
   - Source tracked as `podcast2.0:vtt`, `podcast2.0:srt`, etc.
   - UI shows: "Publisher provided"

2. **Pocket Casts** (Priority 2) - Auto-generated transcripts via public API
   - No authentication required
   - Searches by podcast title, caches show UUID
   - Source tracked as `pocketcasts`
   - UI shows: "Auto-generated"

3. **Whisper** (fallback) - Self-transcribed when no external source available
   - Source tracked as `whisper`
   - UI shows: "Created by cast2md - {model}"

The episode detail page shows the transcript source with appropriate labels. The re-transcribe button only appears for Whisper transcripts (external transcripts are authoritative).

## Usage

### Web Interface

Access the web UI at `http://localhost:8000`

- **Feeds**: Add and manage podcast RSS feeds
- **Episodes**: View discovered episodes and their transcription status
- **Search**: Unified search across episode titles, descriptions, and transcripts
  - **Latest Transcripts carousel**: Browse recent transcripts with podcast artwork when no search query
  - Results grouped by episode with match source badges (keyword/semantic/both)
  - Transcript matches show timestamps - click to jump to that point in the episode
  - **Smart back navigation**: Returns to previous page (search, feed) preserving filters and scroll position
- **Admin**: Monitor system health, worker status, and manage processing queue

#### Episode Workflow

The UI follows a **transcript-first** approach to minimize storage and processing:

1. **Get Transcript** - Tries to download from external sources (Podcasting 2.0, Pocket Casts)
2. **Download Audio** - Falls back to audio download + Whisper transcription if no external transcript

| Episode Status | Feed Page Button | What Happens |
|----------------|------------------|--------------|
| Pending | "Get Transcript" | Tries external transcript providers |
| Pending (after failed transcript) | "Download Audio" | Downloads audio for Whisper |
| Downloaded | "Transcribe" | Queues Whisper transcription |
| Completed | (link) | Opens episode detail with transcript |
| Failed | "Retry" | Re-attempts download |

Status updates in real-time without page reload. Batch operations available for processing entire feeds.

### CLI Commands

```bash
# Initialize database
cast2md init-db

# Add a podcast feed (RSS URL or Apple Podcasts URL)
cast2md add-feed "https://example.com/feed.xml"
cast2md add-feed "https://podcasts.apple.com/us/podcast/the-daily/id1200361736"

# List feeds
cast2md list-feeds

# Poll feed for new episodes
cast2md poll <feed_id>

# List episodes
cast2md list-episodes <feed_id>

# Download an episode
cast2md download <episode_id>

# Transcribe an episode
cast2md transcribe <episode_id>

# Download and transcribe in one step
cast2md process <episode_id>

# Show system status
cast2md status

# Start web server
cast2md serve --host 0.0.0.0 --port 8000

# Backup database
cast2md backup -o /path/to/backup.sql

# Restore database
cast2md restore /path/to/backup.sql

# Start MCP server (for Claude integration)
cast2md mcp              # stdio mode (Claude Code/Desktop)
cast2md mcp --sse        # SSE mode (Claude.ai/remote)

# Distributed transcription (run on remote machines)
cast2md node register --server http://server:8000 --name "M4 Mac"
cast2md node start       # Start transcription worker
cast2md node status      # Check node configuration
cast2md node unregister  # Remove node credentials
```

### MCP Server (Claude Integration)

cast2md includes an MCP (Model Context Protocol) server that enables Claude to search transcripts, manage feeds, and queue episodes for processing.

#### Setup for Claude Code / Claude Desktop

**Local mode** (uses local database):
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

**Remote mode** (connects to server via HTTPS):
```json
{
  "mcpServers": {
    "podcasts": {
      "command": "/path/to/cast2md",
      "args": ["mcp"],
      "env": {
        "MCP_API_URL": "https://your-server.example.com"
      }
    }
  }
}
```

Config locations:
- **Claude Code**: `.mcp.json` in project root
- **Claude Desktop**: `~/Library/Application Support/Claude/claude_desktop_config.json`

#### Available Tools

| Tool | Description |
|------|-------------|
| `search_transcripts` | Full-text search across all transcripts |
| `search_episodes` | Search episodes by title/description |
| `queue_episode` | Queue episode for download/transcription |
| `get_queue_status` | View processing queue status |
| `add_feed` | Add new podcast by RSS URL |
| `refresh_feed` | Poll feed for new episodes |

#### Available Resources

| URI | Description |
|-----|-------------|
| `cast2md://feeds` | List all feeds |
| `cast2md://feeds/{id}` | Feed details + recent episodes |
| `cast2md://episodes/{id}` | Episode details |
| `cast2md://episodes/{id}/transcript` | Full transcript text |
| `cast2md://status` | System status overview |

#### Example Usage

Once configured, you can ask Claude things like:
- "Search my podcasts for discussions about AI"
- "What episodes are in my queue?"
- "Add this podcast feed: https://example.com/feed.xml"
- "Summarize the latest episode about climate change"

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health check |
| `/api/feeds` | GET | List all feeds |
| `/api/feeds` | POST | Add new feed (RSS URL or Apple Podcasts URL) |
| `/api/feeds/{id}` | GET | Get feed details |
| `/api/feeds/{id}` | PATCH | Update feed (custom_title) |
| `/api/feeds/{id}` | DELETE | Remove feed |
| `/api/feeds/{id}/refresh` | POST | Poll feed for new episodes |
| `/api/feeds/{id}/export` | GET | Export all transcripts as ZIP |
| `/api/episodes/{id}` | GET | Get episode details |
| `/api/episodes/{id}/transcript` | GET | Download transcript (format: md, txt, srt, vtt, json) |
| `/api/episodes/{id}/audio` | DELETE | Delete audio file (keeps transcript) |

#### Queue Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/queue/status` | GET | Get queue status and active jobs |
| `/api/queue/episodes/{id}/process` | POST | Queue audio download |
| `/api/queue/episodes/{id}/transcribe` | POST | Queue Whisper transcription |
| `/api/queue/episodes/{id}/transcript-download` | POST | Try external transcript providers |
| `/api/queue/episodes/{id}/retranscribe` | POST | Re-transcribe with current model |
| `/api/queue/batch/feed/{id}/transcript-download` | POST | Batch: try transcripts for all pending |
| `/api/queue/batch/feed/{id}/retranscribe` | POST | Batch: re-transcribe outdated episodes |

#### Feed Response Fields

Feed responses include extended metadata:
- `title`: Original RSS feed title
- `custom_title`: User-defined override (nullable)
- `display_title`: Shows custom_title if set, otherwise title
- `author`: Podcast author from iTunes tags
- `link`: Podcast website URL
- `categories`: Array of category strings
- `itunes_id`: Apple Podcasts ID (if added via iTunes URL)
- `pocketcasts_uuid`: Pocket Casts show UUID (cached after first lookup)

#### Episode Response Fields

Episode responses include transcript tracking:
- `transcript_path`: Path to local markdown file
- `transcript_url`: Podcasting 2.0 transcript URL from RSS (if available)
- `transcript_source`: Where transcript came from (`whisper`, `podcast2.0:vtt`, `podcast2.0:srt`, etc.)
- `transcript_model`: Whisper model used (only set for whisper source)

## Deployment Files

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage Docker build |
| `docker-compose.yml` | Docker Compose configuration |
| `deploy/cast2md.service` | systemd unit file |
| `deploy/install.sh` | Automated installation script |
| `deploy/backup.sh` | Database backup script for cron |

## NFS Setup (Synology)

For storing media on a Synology NAS:

1. **Create NFS share** on Synology for your media folder
2. **NFS Permissions**:
   - Hostname/IP: Your server IP
   - Privilege: Read/Write
   - Squash: Map root to admin
   - Security: sys
3. **Mount on server**:
   ```bash
   echo '<synology-ip>:/volume1/Media/Podcasts /mnt/nas/cast2md nfs defaults,_netdev,nfsvers=4.1,rw,noatime 0 0' >> /etc/fstab
   mount /mnt/nas/cast2md
   ```

For unprivileged LXC containers, mount on the Proxmox host and bind-mount into the container.

## Backup

### Manual Backup

```bash
cast2md backup -o /mnt/nas/cast2md/backups/cast2md_$(date +%Y%m%d).sql
```

### Automated Backup (cron)

```bash
# Add to crontab: backup every 6 hours
0 */6 * * * /opt/cast2md/deploy/backup.sh
```

## Open Tasks / Roadmap

### Backup Scheduling
- [ ] Set up cron job on server for automated database backups
- [ ] Configure backup retention policy

### GUI Polish
- [x] Show notes preview with full modal view
- [x] Extended podcast metadata (author, website, categories)
- [x] Editable feed titles with storage directory renaming
- [x] Two-tier search: episode cards with detail modal showing show notes + transcript matches
- [x] Dark mode support (theme toggle in navigation)
- [x] Latest transcripts carousel on search page
- [x] Smart back navigation preserving context
- [ ] Add transcript viewer/editor
- [ ] Progress indicators for transcription

### Distributed Transcription (Mac as Remote Worker) ✅
Enable fast transcription using Mac with MLX when available:

```
┌─────────────┐                      ┌─────────────┐
│   Server    │◄────── Tailscale ────│    Mac      │
│  (LXC)      │                      │  (worker)   │
│             │  GET /api/jobs/claim │             │
│  job_queue  │─────────────────────►│  MLX fast   │
│  (SQLite)   │◄─────────────────────│transcription│
│             │ POST /api/jobs/done  │             │
└─────────────┘                      └─────────────┘
```

**Implemented!** See [Distributed Transcription Setup Guide](docs/distributed-transcription-setup.md) for details.

- [x] Job queue API endpoints (`/api/nodes/*/claim`, `/api/nodes/jobs/*/complete`, `/api/nodes/jobs/*/fail`)
- [x] Mac worker using mlx-whisper (`cast2md node start`)
- [x] Worker heartbeat and job timeout handling
- [x] Node management UI in Settings
- [x] Status page shows local vs remote workers with current jobs

## Development

```bash
# Clone repository
git clone https://github.com/meltforce/cast2md.git
cd cast2md

# Install with dev dependencies
uv sync

# Run in development mode
uv run cast2md serve --reload

# Run tests
uv run pytest
```

## License

MIT
