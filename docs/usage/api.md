# REST API

cast2md provides a REST API for all operations. Interactive API documentation is available at `/docs` (Swagger UI).

## Base URL

```
http://localhost:8000/api
```

---

## Feeds

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/feeds` | List all feeds |
| `POST` | `/api/feeds` | Add a new feed |
| `GET` | `/api/feeds/{id}` | Get feed details |
| `DELETE` | `/api/feeds/{id}` | Delete feed (moves files to trash) |
| `GET` | `/api/feeds/{id}/episodes` | List episodes for a feed |
| `POST` | `/api/feeds/{id}/refresh` | Refresh feed for new episodes |

### Add Feed

```bash
curl -X POST http://localhost:8000/api/feeds \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/feed.xml"}'
```

Accepts RSS URLs and Apple Podcasts URLs.

---

## Episodes

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/episodes/{id}` | Get episode details |
| `GET` | `/api/episodes/{id}/transcript` | Download transcript |
| `DELETE` | `/api/episodes/{id}/audio` | Delete audio file (keeps transcript) |

---

## Queue

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/queue/status` | Queue status and statistics |
| `POST` | `/api/queue/episodes/{id}/process` | Queue audio download |
| `POST` | `/api/queue/episodes/{id}/transcribe` | Queue Whisper transcription |
| `POST` | `/api/queue/episodes/{id}/transcript-download` | Queue external transcript download |
| `POST` | `/api/queue/episodes/{id}/retranscribe` | Queue re-transcription |
| `POST` | `/api/queue/batch/feed/{id}/transcript-download` | Batch: transcript download for all new episodes |
| `POST` | `/api/queue/batch/feed/{id}/retranscribe` | Batch: re-transcribe outdated episodes |

### Queue Status

```bash
curl http://localhost:8000/api/queue/status
```

Response:

```json
{
  "download_queue": {"queued": 0, "running": 1, "completed": 50, "failed": 2},
  "transcribe_queue": {"queued": 3, "running": 1, "completed": 45, "failed": 0},
  "transcript_download_queue": {"queued": 5, "running": 2, "completed": 100, "failed": 1}
}
```

---

## Search

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/search/semantic` | Hybrid search (keyword + semantic) |
| `GET` | `/api/search/semantic/stats` | Embedding statistics |

### Search

```bash
curl "http://localhost:8000/api/search/semantic?q=protein+and+muscle&mode=hybrid&limit=10"
```

**Query Parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `q` | *(required)* | Search query |
| `mode` | `hybrid` | Search mode: `hybrid`, `semantic`, or `keyword` |
| `limit` | `20` | Maximum results |
| `feed_id` | *(all)* | Limit to specific feed |

---

## Nodes (Distributed Transcription)

### Admin Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/nodes` | List all nodes |
| `POST` | `/api/nodes` | Manually add a node |
| `DELETE` | `/api/nodes/{id}` | Remove a node |
| `POST` | `/api/nodes/{id}/test` | Test node connectivity |

### Node Worker Endpoints

These are called by transcriber node workers:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/nodes/register` | Register new node |
| `POST` | `/api/nodes/{id}/heartbeat` | Keep-alive ping |
| `POST` | `/api/nodes/{id}/claim` | Claim next job |
| `GET` | `/api/nodes/jobs/{job_id}/audio` | Download audio |
| `POST` | `/api/nodes/jobs/{job_id}/complete` | Submit transcript |
| `POST` | `/api/nodes/jobs/{job_id}/fail` | Report failure |
| `POST` | `/api/nodes/jobs/{job_id}/release` | Release job back to queue |
| `POST` | `/api/nodes/{id}/request-termination` | Request pod termination |

All node worker endpoints require the `X-Transcriber-Key` header with the node's API key.

---

## RunPod

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/runpod/status` | Status, active pods, setup states |
| `POST` | `/api/runpod/pods` | Create new pod |
| `DELETE` | `/api/runpod/pods` | Terminate all pods |
| `DELETE` | `/api/runpod/pods/{pod_id}` | Terminate specific pod |
| `GET` | `/api/runpod/pods/{instance_id}/setup-status` | Pod creation progress |
| `PATCH` | `/api/runpod/pods/{instance_id}/persistent` | Set dev mode on/off |
| `DELETE` | `/api/runpod/setup-states/{instance_id}` | Dismiss setup state |
| `GET` | `/api/runpod/models` | List transcription models |
| `POST` | `/api/runpod/models` | Add transcription model |
| `DELETE` | `/api/runpod/models` | Remove transcription model |
| `POST` | `/api/runpod/nodes/cleanup-orphaned` | Clean up orphaned nodes |

### Create Pod

```bash
curl -X POST http://localhost:8000/api/runpod/pods \
  -H "Content-Type: application/json" \
  -d '{"persistent": false}'
```

---

## Settings

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/settings` | Get all settings |
| `POST` | `/api/settings` | Update settings |

---

## System

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/system/health` | Detailed health info |
| `GET` | `/api/status` | System status and episode counts |

### Re-transcription Info

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/queue/retranscribe/info/{feed_id}` | Current model and outdated episode count |
