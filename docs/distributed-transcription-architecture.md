# Distributed Transcription System - Architecture

This document describes the architecture of cast2md's distributed transcription system, which enables remote machines to process transcription jobs in parallel with the main server.

## Overview

The distributed transcription system allows you to leverage multiple machines (M4 MacBooks, GPU-equipped PCs, etc.) to transcribe podcast episodes faster. The main cast2md server acts as a coordinator, while remote "transcriber nodes" poll for work, process jobs locally, and upload results.

```
┌─────────────────────────────────────────────────────────────┐
│                     Main Server (cast2md)                    │
│                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ Job Queue   │  │ Node        │  │ Audio Storage       │  │
│  │ (SQLite)    │  │ Registry    │  │ (filesystem)        │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              RemoteTranscriptionCoordinator          │    │
│  │  - Monitors node heartbeats                          │    │
│  │  - Reclaims stuck jobs                               │    │
│  │  - Tracks node status                                │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              Local Transcription Worker              │    │
│  │  - Processes jobs not claimed by nodes               │    │
│  │  - Works in parallel with remote nodes               │    │
│  └─────────────────────────────────────────────────────┘    │
└──────────────────────────┬──────────────────────────────────┘
                           │
                    HTTP API (pull-based)
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
         ▼                 ▼                 ▼
   ┌───────────┐     ┌───────────┐     ┌───────────┐
   │  Node A   │     │  Node B   │     │  Node C   │
   │  M4 Mac   │     │  GPU PC   │     │  Linux    │
   │           │     │           │     │  Server   │
   └───────────┘     └───────────┘     └───────────┘
```

## Design Principles

### Pull-Based Model

Nodes actively poll the server for work rather than the server pushing jobs to nodes. This design choice provides several benefits:

1. **NAT/Firewall Friendly**: Nodes behind NAT or firewalls work without configuration since they initiate all connections
2. **Natural Load Balancing**: Nodes only request work when ready, preventing overload
3. **Simple Fault Tolerance**: If a node disappears, its job times out and becomes available again
4. **No Node Discovery**: Server doesn't need to know how to reach nodes

### Parallel Processing

The local transcription worker and remote nodes work simultaneously:

- Local worker processes jobs with `assigned_node_id IS NULL`
- When a node claims a job, local worker skips it and moves to the next unclaimed job
- This maximizes throughput, especially when batching many episodes

**Example: 100 episodes queued**
```
Time 0:    100 jobs queued (all unclaimed)
           Local worker starts job #1
Time 5m:   Node joins, claims job #2, starts transcribing
Time 6m:   Local finishes #1, picks #3 (skips claimed #2)
           Both now working in parallel
Time ???:  All 100 done faster with parallel processing
```

### Trusted Network Assumption

The system assumes operation on a trusted network (Tailscale, local LAN):

- Simple API key authentication (no complex auth flows)
- No HTTPS required (network already encrypted via Tailscale/WireGuard)
- API keys generated on registration, stored locally on nodes

## Components

### Server Components

#### 1. TranscriberNode Model (`db/models.py`)

```python
@dataclass
class TranscriberNode:
    id: str                      # UUID
    name: str                    # Human-readable name
    url: str                     # Node's URL for connectivity tests
    api_key: str                 # Shared secret for authentication
    whisper_model: str | None    # Model configured on node
    whisper_backend: str | None  # "mlx" or "faster-whisper"
    status: NodeStatus           # online/offline/busy
    last_heartbeat: datetime     # Last heartbeat timestamp
    current_job_id: int | None   # Job being processed
    priority: int                # Lower = preferred for job assignment
    created_at: datetime
    updated_at: datetime
```

#### 2. TranscriberNodeRepository (`db/repository.py`)

Handles all database operations for nodes:

- `create()` - Register new node
- `get_by_id()`, `get_by_api_key()` - Lookup nodes
- `get_all()`, `get_online()` - List nodes
- `update_status()`, `update_heartbeat()` - Track node state
- `get_stale_nodes()` - Find nodes that missed heartbeats
- `mark_offline()` - Mark node as offline

#### 3. JobRepository Extensions (`db/repository.py`)

New methods for distributed job management:

- `get_next_unclaimed_job()` - Get job not assigned to any node
- `claim_job()` - Assign job to a node
- `unclaim_job()` - Release job from node
- `get_jobs_by_node()` - List jobs for a node
- `reclaim_stale_jobs()` - Reset jobs stuck on offline nodes

#### 4. RemoteTranscriptionCoordinator (`distributed/coordinator.py`)

Background thread that manages the distributed system:

```python
class RemoteTranscriptionCoordinator:
    def _check_nodes(self):
        """Mark nodes offline if no heartbeat within timeout."""

    def _reclaim_stale_jobs(self):
        """Reset jobs running too long on nodes."""
```

Configuration:
- `heartbeat_timeout_seconds`: 60 (default) - Time before node marked offline
- `job_timeout_hours`: 2 (default) - Time before job reclaimed from node
- `check_interval_seconds`: 30 - How often coordinator runs checks

#### 5. Node API Endpoints (`api/nodes.py`)

**Registration & Heartbeat:**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/nodes/register` | POST | Register new node, returns credentials |
| `/api/nodes/{id}/heartbeat` | POST | Keep-alive ping (every 30s) |

**Job Processing:**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/nodes/{id}/claim` | POST | Claim next available job |
| `/api/nodes/jobs/{job_id}/audio` | GET | Download audio file |
| `/api/nodes/jobs/{job_id}/complete` | POST | Submit transcript |
| `/api/nodes/jobs/{job_id}/fail` | POST | Report failure |
| `/api/nodes/jobs/{job_id}/release` | POST | Release job back to queue (on shutdown) |

**Admin:**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/nodes` | GET | List all nodes |
| `/api/nodes` | POST | Manually add node |
| `/api/nodes/{id}` | DELETE | Remove node |
| `/api/nodes/{id}/test` | POST | Test connectivity |

### Node Components

#### 1. NodeConfig (`node/config.py`)

Manages node credentials stored in `~/.cast2md/node.json`:

```json
{
  "server_url": "http://server:8000",
  "node_id": "uuid-from-registration",
  "api_key": "generated-api-key",
  "name": "M4 MacBook"
}
```

#### 2. TranscriberNodeWorker (`node/worker.py`)

Main worker class that:

1. **Polls for jobs** every 5 seconds
2. **Sends heartbeats** every 30 seconds
3. **Processes jobs**:
   - Download audio from server
   - Transcribe using local Whisper
   - Upload transcript to server

```python
class TranscriberNodeWorker:
    def _poll_loop(self):
        """Poll for jobs and process them."""
        job = self._claim_job()
        if job:
            self._process_job(job)

    def _heartbeat_loop(self):
        """Send periodic heartbeats to server."""

    def _process_job(self, job):
        """Download → Transcribe → Upload"""
```

#### 3. Node Status Server (`node/server.py`)

Minimal FastAPI server (port 8001) showing:
- Node configuration
- Current job status
- Worker state

## Data Flow

### Job Lifecycle

```
1. Episode queued for transcription
   └─> job_queue entry created (status=queued, assigned_node_id=NULL)

2. Node polls /api/nodes/{id}/claim
   └─> Server finds unclaimed job
   └─> Updates job: status=running, assigned_node_id=<node>, claimed_at=<now>
   └─> Returns job details + audio URL

3. Node downloads audio via /api/nodes/jobs/{id}/audio
   └─> Server streams audio file

4. Node transcribes locally
   └─> Uses configured Whisper model/backend

5. Node submits result via /api/nodes/jobs/{id}/complete
   └─> Server saves transcript
   └─> Server updates episode status
   └─> Server indexes transcript for search
   └─> Server marks job completed
   └─> Server updates node status to online
```

### Heartbeat Flow

```
Every 30 seconds:
  Node → POST /api/nodes/{id}/heartbeat
       → Server updates last_heartbeat
       → Server marks node online if was offline

Every 30 seconds (coordinator):
  Server checks for stale nodes (no heartbeat > 60s)
       → Marks stale nodes offline
       → Reclaims their jobs (if running > 2h)
```

## Database Schema

### transcriber_node table

```sql
CREATE TABLE transcriber_node (
    id TEXT PRIMARY KEY,              -- UUID
    name TEXT NOT NULL,               -- "M4 MacBook Pro"
    url TEXT NOT NULL,                -- "http://192.168.1.100:8001"
    api_key TEXT NOT NULL,            -- Shared secret
    whisper_model TEXT,               -- Model on node
    whisper_backend TEXT,             -- "mlx" or "faster-whisper"
    status TEXT DEFAULT 'offline',    -- online/offline/busy
    last_heartbeat TEXT,
    current_job_id INTEGER,
    priority INTEGER DEFAULT 10,      -- Lower = preferred
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### job_queue additions

```sql
ALTER TABLE job_queue ADD COLUMN assigned_node_id TEXT;
ALTER TABLE job_queue ADD COLUMN claimed_at TEXT;
```

## Configuration

### Server Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `distributed_transcription_enabled` | bool | false | Enable/disable the system |
| `node_heartbeat_timeout_seconds` | int | 60 | Seconds before node marked offline |
| `remote_job_timeout_hours` | int | 2 | Hours before stuck job reclaimed |

### Node Settings

Nodes use the same Whisper settings as a normal cast2md installation:
- `WHISPER_MODEL` - Model to use (e.g., "large-v3-turbo")
- `WHISPER_BACKEND` - Backend ("auto", "mlx", "faster-whisper")

## Failure Handling

| Scenario | Detection | Resolution |
|----------|-----------|------------|
| Node disappears mid-job | Heartbeat timeout (60s) | Job reclaimed after 2h timeout |
| Node graceful shutdown | Signal handler (SIGTERM/SIGINT) | Job released immediately via API |
| Network fails on upload | Node retry with backoff | Store locally, retry on restart |
| Server restarts | Node continues, resubmits | Accept result if job exists |
| Audio corrupted | Transcription error | Mark failed, can retry |
| Node crashes | No heartbeat | Marked offline, job reclaimed |

## Graceful Shutdown

Both the main server and transcriber nodes implement graceful shutdown to prevent orphaned jobs.

### Server Shutdown

On SIGTERM/SIGINT, the server:
1. Signal handler triggers FastAPI lifespan shutdown
2. Workers are stopped gracefully (30s timeout)
3. On restart, `reset_orphaned_jobs()` resets any jobs left in "running" state back to "queued"

### Node Shutdown

On SIGTERM/SIGINT (or Ctrl+C), the node:
1. Signal handler calls `worker.stop()`
2. If a job is in progress, `_release_current_job()` is called
3. Node POSTs to `/api/nodes/jobs/{job_id}/release`
4. Server resets the job to "queued" state immediately
5. Job becomes available for pickup by another node (or the same node after restart)

This avoids the 2-hour timeout wait for job reclamation when a node is intentionally stopped.

**Release Endpoint:**
```
POST /api/nodes/jobs/{job_id}/release
Authorization: X-Transcriber-Key: <api_key>

Response: {"message": "Job released back to queue"}
```

## Security Considerations

1. **API Key Authentication**: All node requests require `X-Transcriber-Key` header
2. **Network Security**: Designed for trusted networks (Tailscale, LAN)
3. **No Secrets in URLs**: API keys in headers, not URL parameters
4. **Job Ownership**: Nodes can only access jobs assigned to them

## Performance Characteristics

- **Polling Interval**: 5 seconds (configurable)
- **Heartbeat Interval**: 30 seconds
- **Coordinator Check Interval**: 30 seconds
- **Job Timeout**: 2 hours (configurable)
- **Audio Transfer**: Streamed, not buffered entirely in memory

## Limitations

1. **Single Job Per Node**: Each node processes one job at a time
2. **No Job Priorities for Nodes**: All nodes see the same queue (priority ordering)
3. **No Partial Progress**: If node fails mid-transcription, job restarts from scratch
4. **Trust Required**: API keys provide authentication, not authorization granularity
