# cast2md - Project Knowledge

## Deployment

The production server runs on `cast2md` (Tailscale hostname) via Docker Compose. The server has no git repo -- only `docker-compose.yml`, `.env`, and data.

To deploy a tagged release (after CI builds it):
```bash
ssh root@cast2md "cd /opt/cast2md && docker compose pull cast2md && docker compose up -d cast2md"
```

Production uses the `latest` tag, which CI updates on every tagged release.

**Docker Hub** is only updated by CI on tagged releases. Never push dev builds to Docker Hub -- other users could pull an undefined state.

**Releasing a new version:**
1. Bump `version` in `pyproject.toml` to match the new tag (e.g., `"2026.01.1"`)
2. Commit the version bump
3. `git tag 2026.01.1 && git push origin main 2026.01.1`
4. CI builds the Docker image and publishes the PyPI package

**Important:** The `pyproject.toml` version must match the git tag. PyPI rejects duplicate versions, so forgetting to bump it will fail the release.

**Important:** Always test on the dev machine first. Never use the production server for testing -- repeated restarts disrupt workers, nodes, and job state.

## Architecture

- **Production**: Runs entirely via Docker Compose (app + PostgreSQL)
- **Node workers**: Remote transcription nodes connect to the server
- **Local workers**: Download workers and one local transcription worker run in the app container
- **Database**: PostgreSQL with pgvector, runs in Docker (`docker compose up -d`)

### Production Stack

Both PostgreSQL and the cast2md app run as Docker containers:

```bash
# Start/restart the full stack
ssh root@cast2md "cd /opt/cast2md && docker compose up -d"

# View logs
ssh root@cast2md "cd /opt/cast2md && docker compose logs -f cast2md"

# Check status
ssh root@cast2md "docker compose -f /opt/cast2md/docker-compose.yml ps"
```

Configuration is in `/opt/cast2md/.env` (not checked into git). The Docker Compose file reads env vars from `.env` and passes them to the containers.

**Important:** The `.env` file contains secrets (RUNPOD_API_KEY, database credentials). Never commit it. The Docker image is `meltforce/cast2md:<version>`, built by CI.

## Development (Dev Machine)

The dev machine (`jesus`) runs a test instance from the git checkout for fast iteration on migrations, API changes, UI, and worker logic.

### Setup

PostgreSQL runs via Docker Compose (same as production):
```bash
cd ~/projects/cast2md
docker compose up -d postgres
```

The app runs from the local git checkout in a virtualenv:
```bash
.venv/bin/python -m cast2md serve --host 0.0.0.0 --port 8000
```

### Configuration

Dev config is in `.env` (local, not committed):
```
DATABASE_URL=postgresql://cast2md:dev@localhost:5432/cast2md
STORAGE_PATH=./data/podcasts
TEMP_DOWNLOAD_PATH=./data/temp
WHISPER_MODEL=base
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
```

Key difference from production: `DATABASE_URL` points to `localhost:5432` (Docker postgres exposes the port to the host), while in Docker Compose production, the app uses `postgres:5432` (Docker internal DNS).

### Workflow

1. Make code changes
2. Start dev server: `.venv/bin/python -m cast2md serve --host 0.0.0.0 --port 8000`
3. Test at `http://localhost:8000`
4. Stop with Ctrl+C when done

No systemd service -- run on demand. Reinstall after dependency changes:
```bash
.venv/bin/python -m pip install -e .
```

## Development

- Status UI: https://<your-tailnet>/status
- API docs: https://<your-tailnet>/docs

## Documentation

Public docs are at [meltforce.org/cast2md](https://meltforce.org/cast2md), built with **Zensical** (MkDocs Material successor).

- Source: `docs/` directory + `mkdocs.yml`
- CI: `.github/workflows/docs.yml` builds on pushes to `docs/**` or `mkdocs.yml`
- Hosting: GitHub Pages (source: GitHub Actions, not "Deploy from branch")
- Local preview: `pip install zensical && zensical serve`

**Key files:**
- `mkdocs.yml` - Site config and navigation (Zensical reads this natively)
- `docs/CNAME` - Custom domain (`meltforce.org`)
- `docs/internal/` - Internal docs (not in nav, but still publicly accessible by URL)
- `cast2md-requirements.md` - Central requirements document with architecture, data model, and development phases

## Testing


### API Testing

Always test the server API directly via the URL, not by SSH + localhost:
```bash
# Good - direct API call
curl https://<your-tailnet>/api/health

# Bad - unnecessary SSH
ssh root@<server> "curl localhost:8000/api/health"
```

## Transcription

### Backends

The system supports two transcription backends:

| Backend | Use Case | Languages | Speed |
|---------|----------|-----------|-------|
| **Whisper** | Local/server transcription | 99+ languages | Varies by model |
| **Parakeet** | RunPod GPU pods (default) | 25 EU languages | Very fast |

The backend is controlled by `TRANSCRIPTION_BACKEND` environment variable (`whisper` or `parakeet`).

### Model Tracking

Episodes track which model was used via `transcript_model` column (e.g., `parakeet-tdt-0.6b-v3`, `large-v3-turbo`). This is visible on the episode detail page.

### Re-transcription API

API endpoints exist for script-based re-transcription (UI removed):
- `GET /api/queue/retranscribe/info/{feed_id}` - get current model and count
- `POST /api/queue/episodes/{id}/retranscribe` - queue single episode
- `POST /api/queue/batch/feed/{id}/retranscribe` - queue all outdated in feed

## iTunes URL Support

Feeds can be added via Apple Podcasts URLs. The system automatically resolves them to RSS feed URLs.

### How It Works

1. `feed/itunes.py:resolve_feed_url()` detects Apple Podcasts URLs
2. Extracts iTunes ID from URL pattern `podcasts.apple.com/.*/id(\d+)`
3. Calls iTunes Lookup API to get RSS feed URL
4. Stores `itunes_id` on the feed for reference

### Key Files

- `clients/itunes.py` - iTunes API client (`ItunesClient.lookup()`)
- `feed/itunes.py` - URL detection and resolution
- `api/feeds.py:create_feed()` - Calls `resolve_feed_url()` before validation

## Transcript Sources

Episodes track where their transcript came from via `transcript_source` column:

- `whisper` - Self-transcribed using Whisper
- `podcast2.0:vtt` - Downloaded from publisher (WebVTT format)
- `podcast2.0:srt` - Downloaded from publisher (SRT format)
- `podcast2.0:json` - Downloaded from publisher (JSON format)
- `podcast2.0:text` - Downloaded from publisher (plain text)
- `pocketcasts` - Auto-generated by Pocket Casts
- `NULL` - Legacy episodes (before this feature)

### Transcript-First Workflow

When a feed is added or refreshed, the system queues `TRANSCRIPT_DOWNLOAD` jobs:

1. `feed/discovery.py:discover_new_episodes()` queues `TRANSCRIPT_DOWNLOAD` jobs (not `DOWNLOAD`)
2. `worker/manager.py:_process_transcript_download_job()` tries external providers
3. If transcript found: saves it, marks episode `completed` (no audio needed)
4. If not found but retriable: episode becomes `awaiting_transcript`
5. If retries exhausted: episode becomes `needs_audio` for manual audio download

This is storage-efficient - audio is only downloaded when transcripts aren't available externally.

### Provider Priority

1. **Podcast20Provider** - RSS `<podcast:transcript>` tags (authoritative)
2. **PocketCastsProvider** - Auto-generated transcripts (fallback)
3. **Whisper** - Self-transcription (only after audio download)

### Pocket Casts Provider

Uses public Pocket Casts API (no authentication required):

1. Search API: `POST podcast-api.pocketcasts.com/discover/search`
2. Show notes API: `GET podcast-api.pocketcasts.com/mobile/show_notes/full/{uuid}`
   - Returns redirect to static JSON, must follow redirects
3. Downloads from `pocket_casts_transcripts[]` array (VTT format)

The provider:
- Searches by feed title, matches by author
- Caches `pocketcasts_uuid` on feed after first successful search
- Matches episodes by title similarity + published date within 24h

### Adding New Providers

1. Create `src/cast2md/transcription/providers/newprovider.py`
2. Implement `TranscriptProvider` base class:
   - `source_id` property (e.g., `"newprovider"`)
   - `can_provide(episode, feed)` - check if provider applies
   - `fetch(episode, feed)` - download and return `TranscriptResult`
3. Register in `providers/__init__.py`:
   ```python
   _providers = [
       Podcast20Provider(),
       PocketCastsProvider(),
       NewProvider(),  # Add here
   ]
   ```

### Key Files

- `clients/pocketcasts.py` - Pocket Casts API client
- `transcription/providers/base.py` - `TranscriptProvider` abstract base class
- `transcription/providers/podcast20.py` - Podcasting 2.0 implementation
- `transcription/providers/pocketcasts.py` - Pocket Casts implementation
- `transcription/providers/__init__.py` - Provider registry and `try_fetch_transcript()`
- `transcription/formats.py` - VTT/SRT/JSON/text parsers
- `worker/manager.py:_process_transcript_download_job()` - Transcript download handler
- `worker/manager.py:_queue_transcription()` - Post-download transcription (Whisper fallback)

## Web UI Workflow

### Feed Episode List (feed_detail.html)

The episode list uses a **transcript-first** approach with real-time status updates:

#### Button Behavior

| Episode Status | Button | Action |
|----------------|--------|--------|
| `new` | "Get Transcript" | Queues `TRANSCRIPT_DOWNLOAD` job |
| `awaiting_transcript` | "Download Audio" | Queues `DOWNLOAD` job |
| `needs_audio` | "Download Audio" | Queues `DOWNLOAD` job |
| `audio_ready` | "Transcribe" | Queues `TRANSCRIBE` job (Whisper) |
| `failed` | "Retry" | Queues `DOWNLOAD` job |
| `downloading`, `transcribing` | "..." (disabled) | No action, status shown in badge |
| `completed` | (none) | Link to episode detail |

#### Transcript Download Flow

1. User clicks "Get Transcript" → `POST /api/queue/episodes/{id}/transcript-download`
2. Button becomes disabled ("..."), status badge shows "queued"
3. Worker tries Podcast20Provider, then PocketCastsProvider
4. If found: episode marked `completed`, button becomes link to detail
5. If not found: episode becomes `awaiting_transcript` or `needs_audio`, button shows "Download Audio"

When no external transcript is available, the button changes to "Download Audio" which queues the full audio download + Whisper transcription pipeline.

#### Real-time Status Updates

The feed page polls `/api/feeds/{id}/episodes` every 2 seconds while jobs are in progress:

- `startStatusPolling()` - Starts interval timer
- `stopStatusPolling()` - Stops when all visible episodes are in a stable state (completed/failed/new/needs_audio)
- `pollEpisodeStatus()` - Fetches current status and updates DOM
- `updateEpisodeRow()` - Updates badge, checkbox, and action button

Polling uses visible episode IDs from DOM (not template-rendered array) to handle pagination correctly.

#### Batch Operations

- "Get All Transcripts" button queues all new episodes via `POST /api/queue/batch/feed/{id}/transcript-download`

### Episode Detail Page (episode_detail.html)

Shows full episode info with transcript viewer and manual action buttons:

| Status | Available Actions |
|--------|-------------------|
| `new` | "Try Transcript Download", "Download Audio" |
| `awaiting_transcript` | "Download Audio" |
| `needs_audio` | "Download Audio" |
| `audio_ready` | "Queue Transcription" |
| `completed` | "Delete Audio" (if audio exists), "Download Audio" (if deleted) |
| `failed` | "Retry" |

### Queue API Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/queue/episodes/{id}/process` | Download audio (creates `DOWNLOAD` job) |
| `POST /api/queue/episodes/{id}/transcribe` | Whisper transcription (creates `TRANSCRIBE` job) |
| `POST /api/queue/episodes/{id}/transcript-download` | Try external providers (creates `TRANSCRIPT_DOWNLOAD` job) |
| `POST /api/queue/episodes/{id}/retranscribe` | Re-transcribe with current model |
| `POST /api/queue/batch/feed/{id}/transcript-download` | Batch transcript download for all new episodes |
| `POST /api/queue/batch/feed/{id}/retranscribe` | Batch re-transcribe for outdated episodes |

## Audio Management

Episodes with external transcripts don't need audio files. The audio can be deleted to save space:

- `DELETE /api/episodes/{id}/audio` - Deletes audio file, keeps `audio_url` for re-download
- Only allowed if episode has a transcript
- Episode detail page shows "Delete Audio" / "Download Audio" buttons accordingly

## Feed Deletion and Trash

When a feed is deleted, files are moved to trash instead of being permanently deleted:

### How It Works

1. User clicks "Delete Feed" on feed detail page
2. Confirmation dialog requires typing "delete"
3. `DELETE /api/feeds/{id}` moves files to trash, then deletes DB records
4. Server auto-cleans trash entries older than 30 days on startup

### Trash Structure

```
{storage_path}/trash/{feed_slug}_{feed_id}_{timestamp}/
├── audio/
│   └── {feed_id}/
│       └── *.mp3
└── transcripts/
    └── {feed_id}/
        └── *.json
```

### Key Files

- `storage/filesystem.py` - `move_feed_to_trash()`, `cleanup_old_trash()`
- `api/feeds.py:delete_feed()` - Calls trash functions before DB deletion
- `main.py:lifespan()` - Runs cleanup on server startup

### Limitations

- DB records are deleted immediately (no restore from trash)
- Only files are preserved in trash
- Manual restore requires re-adding feed and copying files back

## Unified Search

The main search (`/search`) provides unified search across both episode metadata and transcript content. Users can find episodes by title/description or by what was said in the transcript.

### Result Types

Search returns two types of results:

1. **Episode Matches** (`result_type: "episode"`)
   - Matches episode title or description
   - Shows "title" badge
   - Links directly to episode (no timestamp)

2. **Transcript Matches** (`result_type: "transcript"`)
   - Matches content within transcripts
   - Shows "keyword", "semantic", or "both" badge
   - Links to episode with timestamp

### How It Works

1. **Hybrid Search**: Combines PostgreSQL full-text search with vector similarity using Reciprocal Rank Fusion (RRF)
2. **Embeddings**: Uses `sentence-transformers` with multilingual model (384-dim, ~470MB)
3. **Vector Storage**: pgvector extension with HNSW index for fast approximate nearest neighbor search
4. **Episode Search**: Searches `episode_search` table for title/description matches

### Embedding Model

Uses `paraphrase-multilingual-MiniLM-L12-v2` for German language support:
- 50+ languages including German
- Understands semantic similarity (e.g., "kaltbaden" ≈ "eisbaden")
- 384 dimensions, ~470MB model size
- Configured in `search/embeddings.py`

### Segment Merging

Transcripts (both Whisper and external) can have word-level timestamps where each word is a separate segment. The system automatically merges these into phrases:

- Merging happens during indexing (FTS and embeddings) and display
- Phrase boundaries: punctuation, pauses (>1.5s), or max 200 chars
- Improves both search quality (fewer noisy results) and readability
- See `search/parser.py:merge_word_level_segments()`

### Architecture

```
Query → Generate embedding (~20ms)
     ↓
     ├── Episode title/description FTS (fast)
     ├── Transcript segment tsvector search (fast)
     └── pgvector HNSW search (fast)
     ↓
     RRF fusion → Combined results (episodes + transcript segments)
```

### Key Files

- `search/embeddings.py` - Embedding generation, model config
- `search/parser.py` - `merge_word_level_segments()` for phrase merging
- `search/repository.py` - `hybrid_search()`, `index_episode_embeddings()`
- `api/search.py` - `/api/search/semantic` endpoint
- `mcp/tools.py` - `semantic_search` MCP tool
- `worker/manager.py` - Embedding worker (processes `EMBED` jobs)

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/search/semantic?q={query}&mode={mode}` | Hybrid search (mode: hybrid/semantic/keyword) |
| `GET /api/search/semantic/stats` | Embedding statistics |

### MCP Tool

```python
semantic_search(query="protein and muscle building", mode="hybrid")
```

### Reindexing

```bash
# Reindex FTS only
cast2md reindex-transcripts

# Reindex FTS and regenerate embeddings (needed after model change)
cast2md reindex-transcripts --embeddings
```

### Startup Behavior

- **Embeddings**: Persisted in PostgreSQL (survives restarts)
- **Model loading**: ~3 seconds on first semantic query after restart
- **Background worker**: Automatically generates embeddings for new transcripts

### pgvector Notes

- Uses HNSW index for fast approximate nearest neighbor search
- Vector column defined as `vector(384)` matching embedding dimension
- Cosine distance used for similarity: `embedding <=> query_embedding`

## UI Guidelines

### Tooltips

Always use custom CSS tooltips instead of native browser `title` attributes. Native tooltips are unreliable across browsers and have inconsistent display timing.

Implementation pattern (see `base.html`):
```css
.element-with-tooltip {
    position: relative;
    cursor: help;
}
.element-with-tooltip::after {
    content: attr(title);
    position: absolute;
    bottom: 100%;
    left: 50%;
    transform: translateX(-50%);
    padding: 0.4rem 0.6rem;
    background: var(--pico-card-background-color);
    color: var(--pico-color);
    font-size: 0.75rem;
    border-radius: 4px;
    white-space: nowrap;
    opacity: 0;
    visibility: hidden;
    transition: opacity 0.15s, visibility 0.15s;
    z-index: 1000;
    pointer-events: none;
}
.element-with-tooltip:hover::after {
    opacity: 1;
    visibility: visible;
}
```

The `title` attribute still holds the tooltip text (for accessibility), but CSS `::after` with `content: attr(title)` renders it visually.

## RunPod Afterburner

On-demand GPU transcription worker for processing large backlogs. Uses **Parakeet TDT 0.6B v3** by default for fast transcription (supports 25 European languages including German).

### Quick Start

```bash
source deploy/afterburner/.env
python deploy/afterburner/afterburner.py --dry-run  # Validate config
python deploy/afterburner/afterburner.py --test     # Test connectivity
python deploy/afterburner/afterburner.py            # Process queue
```

### Key Files

- `deploy/afterburner/afterburner.py` - Main script (installs NeMo toolkit for Parakeet)
- `deploy/afterburner/startup.sh` - Reference copy of startup script
- `deploy/afterburner/.env.example` - Environment configuration template
- `deploy/afterburner/Dockerfile` - Custom Docker image for RunPod
- `deploy/afterburner/IMAGE.md` - Docker image build documentation

### Docker Image

RunPod pods use a custom Docker image (`meltforce/cast2md-afterburner:cuda124`) with pre-installed dependencies:

| Component | Notes |
|-----------|-------|
| CUDA 12.4.1 | Runtime only (not devel) |
| PyTorch 2.4.0+cu124 | Pinned for CUDA compatibility |
| NeMo toolkit | Latest version (CUDA graphs handled at runtime) |
| Parakeet model | Pre-downloaded (~600MB) |
| faster-whisper | Fallback for Whisper models |

**CUDA Graphs**: NeMo 2.6+ auto-detects driver/toolkit incompatibility and disables CUDA graphs at runtime. Additionally, `TranscriptionService._disable_cuda_graphs()` handles this programmatically. No build-time env vars needed.

This may reduce speed from ~87x to ~60-70x realtime but ensures stability across different GPU/driver combinations.

**Building**: The image is built automatically via GitHub Actions when `deploy/afterburner/Dockerfile` changes. See `deploy/afterburner/IMAGE.md` for manual build instructions.

### GPU Validation

During pod setup, a GPU smoke test runs before the worker starts (Parakeet only). It transcribes 1 second of silence to catch CUDA errors early, preventing a broken GPU from burning through the job queue.

- Runs between "Installing cast2md" and "Registering node" setup steps
- Timeout: 120 seconds (model is pre-loaded in image)
- If it fails, the pod is marked as FAILED in the admin UI
- Combined with the circuit breaker (see Auto-Termination), this provides defense in depth

### Transcription Models

RunPod pods default to Parakeet but can use Whisper models. Models are configurable via the RunPod settings page:

- **Manage Models**: Add/remove models in "Manage Transcription Models" section
- **Custom Models**: Add any Whisper or Parakeet model by ID
- **API**: `GET/POST/DELETE /api/runpod/models`

Default models:
- `parakeet-tdt-0.6b-v3` - Fast, 25 EU languages (default)
- `large-v3-turbo`, `large-v3`, `large-v2`, `medium`, `small` - Whisper models

### Node Worker Prefetch

The node worker uses a **3-slot prefetch queue** to keep audio ready for instant transcription. This is important for Parakeet which transcribes faster than download speed.

### Job State Synchronization

When the server restarts while nodes are processing jobs, the system maintains job state consistency:

**Server Restart Handling:**
- `reset_running_jobs()` only resets jobs with `assigned_node_id IS NULL` (local server jobs)
- Remote node jobs keep their assignment - the coordinator's timeout handles truly dead nodes
- This prevents the old bug where restarts caused nodes to get 403 "Job not assigned to this node" errors

**Heartbeat Resync:**
Nodes report their state in each heartbeat (every 30s):
- `current_job_id` - The job currently being transcribed
- `claimed_job_ids` - All jobs the node has claimed (current + prefetch queue)

The server uses this to:
1. **Resync lost assignments** - If a node reports a job that lost its `assigned_node_id` (e.g., after server restart), the assignment is restored
2. **Release orphaned jobs** - Jobs assigned to a node but not in its `claimed_job_ids` are released back to the queue (handles node restarts losing prefetch state)
3. **Update node status** - Nodes marked offline come back to busy/online after heartbeat

**Key Files:**
- `db/repository.py` - `reset_running_jobs()`, `resync_job()`, `release_job()`
- `api/nodes.py` - Heartbeat handler with resync and orphan detection
- `node/worker.py` - `_heartbeat_loop()` sends job state

### Auto-Termination

Node workers have four auto-termination conditions (all respect persistent/dev mode):

1. **Empty Queue** - Terminate after N consecutive empty queue checks (default: 2 checks, 60s apart)
   - Same behavior as CLI afterburner
   - Env: `NODE_REQUIRED_EMPTY_CHECKS=2`, `NODE_EMPTY_QUEUE_WAIT=60`

2. **Idle Timeout** - Safety net if jobs exist but can't be claimed (default: 10 minutes)
   - Catches stuck/failing jobs, node assignment issues
   - Env: `NODE_IDLE_TIMEOUT_MINUTES=10` (0 to disable)

3. **Server Unreachable** - Terminate if server crashes (default: 5 minutes)
   - Protects against burning money if server goes down
   - Env: `NODE_SERVER_UNREACHABLE_MINUTES=5`

4. **Circuit Breaker** - Terminate after N consecutive transcription failures (default: 3)
   - Protects against broken GPU burning through the job queue
   - Checked after every job (not just on empty queue)
   - Counter resets on any successful transcription
   - In persistent/dev mode: logs ERROR but does not terminate
   - Env: `NODE_MAX_CONSECUTIVE_FAILURES=3` (0 to disable)

**Persistent/Dev Mode**: Set `NODE_PERSISTENT=1` to disable all auto-termination. This is automatically set when:
- Creating pods with `persistent=True` via API
- Using `--keep-alive` flag with CLI afterburner

**Server-Controlled Termination**: When a node worker decides to auto-terminate, it notifies the server first instead of just exiting. This prevents orphaned setup states.

Flow:
1. Worker detects termination condition (empty queue, idle, server unreachable, circuit breaker)
2. Worker calls `POST /api/nodes/{node_id}/request-termination`
3. Server extracts instance_id from node name pattern "RunPod Afterburner {id}"
4. Server releases any jobs claimed by the node back to queue
5. Server terminates pod via RunPod API
6. Server cleans up: setup state, node registration, pod run record
7. Worker is killed when pod terminates (or exits gracefully if termination fails)

The bash watchdog (created during pod setup) becomes a backup mechanism only - it catches cases where the worker crashes without notifying the server.

**API Endpoint**: `POST /api/nodes/{node_id}/request-termination`
- Requires `X-Transcriber-Key` header (node's API key)
- Returns `{"status": "ok", "terminated": true}` on success
- Returns `{"status": "ignored", "terminated": false}` for non-RunPod nodes
- Jobs are released before termination to prevent orphaned work

**Automatic Cleanup**: Orphaned RunPod nodes are cleaned up automatically:
- On server startup (`main.py:lifespan()`)
- Manual trigger: `POST /api/runpod/nodes/cleanup-orphaned`
- Catches pods that crashed or terminated without notifying server

### Pod Setup Architecture

There are two ways to create RunPod pods:

1. **Server-side** (`runpod_service.py`): Pods self-setup via a startup script that calls back to the server's `/api/runpod/pods/{id}/setup-progress` endpoint. No SSH or Tailscale CLI needed on the server.
2. **CLI** (`deploy/afterburner/afterburner.py`): Uses SSH from the local machine to set up pods. Requires Tailscale on the local machine.

Both paths result in the same pod configuration. The server-side path was introduced because the server runs in Docker (no Tailscale CLI access).

### Tailscale Userspace Networking

RunPod containers don't have `/dev/net/tun`, so Tailscale must run in **userspace mode**. This applies to both setup paths and has significant implications:

#### 1. No TUN Interface

```bash
# This is required - can't use default TUN mode
tailscaled --tun=userspace-networking --state=/var/lib/tailscale/tailscaled.state
```

#### 2. Inbound Connections Work Normally

Tailscale SSH works fine because `tailscaled` handles incoming connections:

```bash
# This works once pod is on Tailnet
ssh root@<pod-hostname>
```

#### 3. Outbound Connections Need HTTP Proxy

Applications can't directly connect to Tailscale IPs. Must use the HTTP proxy:

```bash
# Start tailscaled WITH the proxy
tailscaled --tun=userspace-networking --outbound-http-proxy-listen=localhost:1055 &

# Use proxy for outbound traffic
curl -x http://localhost:1055 http://100.x.x.x:8000/api/health

# Or set environment variable
http_proxy=http://localhost:1055 some-command
```

#### 4. HTTP Proxy Doesn't Support HTTPS CONNECT

**Critical limitation**: The proxy only handles plain HTTP. HTTPS fails:

```bash
# Works (HTTP)
curl -x http://localhost:1055 http://server:8000/api/health

# Fails (HTTPS - no CONNECT tunneling)
curl -x http://localhost:1055 https://server/api/health
```

**Solution**: Use HTTP on port 8000 for internal Tailscale traffic. It's still encrypted by Tailscale's WireGuard tunnel.

#### 5. MagicDNS Not Available

`*.ts.net` hostnames don't resolve in userspace mode. Must use `/etc/hosts`:

```bash
echo '100.x.x.x server.tailnet.ts.net' >> /etc/hosts
```

This is why `CAST2MD_SERVER_IP` environment variable is required.

#### 6. Pod Detection with Multiple Orphaned Hosts (CLI only)

Tailscale keeps offline hosts visible. When hostname is taken, it adds `-1`, `-2` suffixes. The CLI afterburner handles this by:

1. Matching hostname prefix (`runpod-afterburner*`)
2. Filtering for `Online=true`
3. Sorting by `Created` timestamp (newest first)
4. Verifying SSH connectivity before proceeding

The server-side path does not use Tailscale peer detection — it uses the pod self-setup HTTP callback instead.

### Parallel Execution

The CLI supports parallel execution. Run multiple instances simultaneously:

```bash
# Each generates unique instance ID (e.g., "a3f2")
python deploy/afterburner/afterburner.py &
python deploy/afterburner/afterburner.py &
python deploy/afterburner/afterburner.py &

# Terminate all
python deploy/afterburner/afterburner.py --terminate-all
```

### Debugging Tips (via Tailscale SSH)

Pods run Tailscale SSH, so you can connect for manual debugging:

```bash
# Check if proxy is listening
ssh root@<pod-hostname> "ss -tlnp | grep 1055"

# Test proxy connectivity
ssh root@<pod-hostname> "curl -x http://localhost:1055 http://<server-ip>:8000/api/health"

# Check Tailscale status
ssh root@<pod-hostname> "tailscale status"

# View node worker logs
ssh root@<pod-hostname> "tail -100 /tmp/cast2md-node.log"
```

### Server-Side RunPod Management

The server includes a RunPod service for managing GPU workers via API. This enables future admin UI integration.

#### Enabling Server-Side RunPod

1. Install optional dependency: `pip install cast2md[runpod]`
2. Set environment variables:
   ```bash
   RUNPOD_API_KEY=...           # Required
   RUNPOD_SERVER_URL=https://<your-tailnet>
   RUNPOD_SERVER_IP=100.x.x.x   # Tailscale IP
   ```
3. Ensure a RunPod Secret named `ts_auth_key` exists in your RunPod account (used by pods for Tailscale auth)
4. Enable in settings: `runpod_enabled=true`

#### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/runpod/status` | GET | Status, active pods, setup states |
| `/api/runpod/pods` | POST | Create new pod (async) |
| `/api/runpod/pods/{instance_id}/setup-status` | GET | Track pod creation progress |
| `/api/runpod/pods` | DELETE | Terminate all pods |
| `/api/runpod/pods/{pod_id}` | DELETE | Terminate specific pod |
| `/api/runpod/pods/{instance_id}/persistent` | PATCH | Set dev mode (persistent) on/off |
| `/api/runpod/setup-states/{instance_id}` | DELETE | Dismiss a setup state |

#### Dev Mode

For development and debugging, pods can be created in **persistent mode** which:
- Prevents auto-termination after processing
- Allows updating code without recreating the pod
- Persists setup state across server restarts

**Enabling dev mode on a running pod:**

```bash
# Set dev mode on (prevents auto-termination, allows code updates)
curl -X PATCH https://<your-tailnet>/api/runpod/pods/{instance_id}/persistent \
  -H "Content-Type: application/json" -d '{"persistent": true}'

# Disable dev mode
curl -X PATCH https://<your-tailnet>/api/runpod/pods/{instance_id}/persistent \
  -H "Content-Type: application/json" -d '{"persistent": false}'
```

Dev mode is useful for:
- Debugging transcription issues
- Extended monitoring

**Setup state persistence:**

Pod setup states are stored in the database (`pod_setup_states` table) and survive server restarts. This means:
- Pods created before a restart are still tracked after restart
- Failed states can be dismissed via the API
- Persistent pods remain visible in the status UI

#### Key Files

- `src/cast2md/services/runpod_service.py` - Pod lifecycle management
- `src/cast2md/api/runpod.py` - REST API endpoints
- `src/cast2md/config/settings.py` - RunPod settings (runpod_* prefix)

#### Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `runpod_enabled` | `false` | Master switch |
| `runpod_max_pods` | `3` | Max concurrent pods |
| `runpod_auto_scale` | `false` | Auto-start on queue growth |
| `runpod_scale_threshold` | `10` | Queue depth to trigger auto-scale |
| `runpod_gpu_type` | `NVIDIA RTX A5000` | Preferred GPU |
| `runpod_blocked_gpus` | `NVIDIA GeForce RTX 4090,NVIDIA GeForce RTX 4080,NVIDIA L4` | Comma-separated GPU blocklist |
| `runpod_whisper_model` | `parakeet-tdt-0.6b-v3` | Transcription model for pods |
| `runpod_idle_timeout_minutes` | `10` | Auto-terminate pods after idle for N minutes (0 to disable) |

#### GPU Compatibility

**Important:** RTX 40-series consumer GPUs and certain datacenter GPUs have CUDA compatibility issues with NeMo/Parakeet, causing `CUDA error 35` during transcription. These GPUs work fine with Whisper but fail with Parakeet.

**Working GPUs for Parakeet:**
- NVIDIA RTX A5000 (~$0.20-0.25/hr, ~87x realtime)
- NVIDIA RTX A6000
- NVIDIA RTX A4000
- NVIDIA GeForce RTX 3090
- NVIDIA L40

**Blocked GPUs (default blocklist):**
- NVIDIA GeForce RTX 4090
- NVIDIA GeForce RTX 4080
- NVIDIA L4

The blocklist is applied during pod creation and fallback selection. Blocked GPUs are automatically skipped. To modify:

```bash
# Add to .env or systemd environment
runpod_blocked_gpus="NVIDIA GeForce RTX 4090,NVIDIA GeForce RTX 4080,NVIDIA L4"
```
