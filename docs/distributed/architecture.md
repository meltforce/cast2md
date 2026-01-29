# Distributed Transcription Architecture

This document describes the architecture of cast2md's distributed transcription system, which enables remote machines to process transcription jobs in parallel with the main server.

## Overview

The distributed transcription system allows you to leverage multiple machines (M4 MacBooks, GPU PCs, RunPod pods) to transcribe podcast episodes faster. The main cast2md server acts as a coordinator, while remote "transcriber nodes" poll for work, process jobs locally, and upload results.

```
┌─────────────────────────────────────────────────────────────┐
│                     Main Server (cast2md)                    │
│                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ Job Queue   │  │ Node        │  │ Audio Storage       │  │
│  │ (PostgreSQL)│  │ Registry    │  │ (filesystem)        │  │
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
   │  M4 Mac   │     │  GPU PC   │     │  RunPod   │
   └───────────┘     └───────────┘     └───────────┘
```

---

## Design Principles

### Pull-Based Model

Nodes actively poll the server for work rather than the server pushing jobs. This provides:

1. **NAT/Firewall Friendly** -- nodes behind NAT work without configuration since they initiate all connections
2. **Natural Load Balancing** -- nodes only request work when ready, preventing overload
3. **Simple Fault Tolerance** -- if a node disappears, its job times out and becomes available again
4. **No Node Discovery** -- server doesn't need to know how to reach nodes

### Parallel Processing

The local transcription worker and remote nodes work simultaneously:

- Local worker processes jobs with `assigned_node_id IS NULL`
- When a node claims a job, local worker skips it and moves to the next unclaimed job
- This maximizes throughput when batching many episodes

### Trusted Network Assumption

The system assumes operation on a trusted network (Tailscale, local LAN):

- Simple API key authentication (no complex auth flows)
- No HTTPS required (network already encrypted via Tailscale/WireGuard)
- API keys generated on registration, stored locally on nodes

---

## Server Components

### TranscriberNode Model

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
```

### RemoteTranscriptionCoordinator

Background thread that manages the distributed system:

- **Node health monitoring** -- marks nodes offline if no heartbeat within timeout
- **Job reclamation** -- resets jobs running too long on nodes
- **Check interval**: 30 seconds

### Node API Endpoints

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
| `/api/nodes/jobs/{job_id}/release` | POST | Release job back to queue |

**Admin:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/nodes` | GET | List all nodes |
| `/api/nodes` | POST | Manually add node |
| `/api/nodes/{id}` | DELETE | Remove node |
| `/api/nodes/{id}/test` | POST | Test connectivity |

---

## Node Components

### NodeConfig

Manages node credentials stored in `~/.cast2md/node.json`:

```json
{
  "server_url": "http://server:8000",
  "node_id": "uuid-from-registration",
  "api_key": "generated-api-key",
  "name": "M4 MacBook"
}
```

### TranscriberNodeWorker

Main worker class that:

1. **Polls for jobs** every 5 seconds
2. **Sends heartbeats** every 30 seconds
3. **Processes jobs**: download audio -> transcribe with local Whisper -> upload transcript

### Node Prefetch Queue

The node worker uses a **3-slot prefetch queue** to keep audio ready for instant transcription. This is important for backends like Parakeet that transcribe faster than download speed.

---

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
       → Reclaims their jobs (if running > timeout)
```

### Job State Synchronization

When the server restarts while nodes are processing jobs:

- `reset_running_jobs()` only resets jobs with `assigned_node_id IS NULL` (local server jobs)
- Remote node jobs keep their assignment -- the coordinator's timeout handles truly dead nodes
- Nodes report state in each heartbeat (`current_job_id`, `claimed_job_ids`) enabling resync

---

## Failure Handling

| Scenario | Detection | Resolution |
|----------|-----------|------------|
| Node disappears mid-job | Heartbeat timeout (60s) | Job reclaimed after timeout |
| Node graceful shutdown | SIGTERM/SIGINT handler | Job released immediately via API |
| Network fails on upload | Node retry with backoff | Store locally, retry on restart |
| Server restarts | Node continues, resubmits | Accept result if job exists |
| Audio corrupted | Transcription error | Mark failed, can retry |
| Node crashes | No heartbeat | Marked offline, job reclaimed |

### Graceful Shutdown

**Server:** On SIGTERM/SIGINT, workers are stopped gracefully. On restart, `reset_orphaned_jobs()` resets jobs left in "running" state.

**Node:** On SIGTERM/SIGINT (or Ctrl+C), the node releases its current job back to the queue via the release API, making it immediately available for another worker.

---

## Security

1. **API Key Authentication** -- all node requests require `X-Transcriber-Key` header
2. **Network Security** -- designed for trusted networks (Tailscale, LAN)
3. **No Secrets in URLs** -- API keys in headers, not URL parameters
4. **Job Ownership** -- nodes can only access jobs assigned to them

---

## Performance Characteristics

| Parameter | Value |
|-----------|-------|
| Polling interval | 5 seconds |
| Heartbeat interval | 30 seconds |
| Coordinator check interval | 30 seconds |
| Default job timeout | 30 minutes |
| Audio transfer | Streamed, not buffered in memory |
| Prefetch queue | 3 slots |

---

## Limitations

1. **Single Job Per Node** -- each node processes one job at a time
2. **No Job Priorities for Nodes** -- all nodes see the same queue (priority ordering)
3. **No Partial Progress** -- if node fails mid-transcription, job restarts from scratch
4. **Trust Required** -- API keys provide authentication, not fine-grained authorization
