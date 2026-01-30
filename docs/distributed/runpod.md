# RunPod GPU Workers

On-demand GPU transcription workers for processing large backlogs. Uses **Parakeet TDT 0.6B v3** by default for fast transcription (~100x realtime).

## Overview

RunPod pods are managed by the cast2md server. They:

1. Start on demand via the web UI or API
2. Connect back to the server via Tailscale
3. Register as transcriber nodes
4. Process jobs from the queue
5. Auto-terminate when the queue is empty

## Prerequisites

- RunPod account with API key
- Tailscale account with auth key
- cast2md server accessible via Tailscale

## Configuration

### Server Environment

```bash
# Required
RUNPOD_ENABLED=true
RUNPOD_API_KEY=your_runpod_api_key

# Server connection (for pods to reach the server)
RUNPOD_SERVER_URL=https://your-server.ts.net
RUNPOD_SERVER_IP=100.x.x.x    # Tailscale IP (required, MagicDNS unavailable in pods)
```

### Tailscale Auth Key

The Tailscale auth key must be configured as a **RunPod Secret**, not a server environment variable.

1. Go to [RunPod Console → Settings → Secrets](https://www.runpod.io/console/user/secrets)
2. Create a secret named exactly **`ts_auth_key`**
3. Set the value to your Tailscale auth key (`tskey-auth-...`)

Pods reference this secret via `{{ RUNPOD_SECRET_ts_auth_key }}` in their startup template. The key is injected as `TS_AUTH_KEY` at pod startup.

### Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `runpod_enabled` | `false` | Master switch |
| `runpod_max_pods` | `3` | Maximum concurrent pods |
| `runpod_auto_scale` | `false` | Auto-start on queue growth |
| `runpod_scale_threshold` | `10` | Queue depth to trigger auto-scale |
| `runpod_gpu_type` | `NVIDIA RTX A5000` | Preferred GPU |
| `runpod_blocked_gpus` | *(see below)* | GPUs to exclude |
| `runpod_whisper_model` | `parakeet-tdt-0.6b-v3` | Default model |
| `runpod_idle_timeout_minutes` | `10` | Auto-terminate idle pods |

---

## Usage

### Starting Pods

=== "Web UI"

    Go to Settings -> RunPod -> "Create Pod"

=== "API"

    ```bash
    curl -X POST http://localhost:8000/api/runpod/pods \
      -H "Content-Type: application/json" \
      -d '{"persistent": false}'
    ```

=== "CLI Afterburner"

    ```bash
    source deploy/afterburner/.env
    python deploy/afterburner/afterburner.py
    ```

### Monitoring

Check pod status:

```bash
curl http://localhost:8000/api/runpod/status
```

Track pod creation progress:

```bash
curl http://localhost:8000/api/runpod/pods/{instance_id}/setup-status
```

### Terminating

```bash
# Terminate all pods
curl -X DELETE http://localhost:8000/api/runpod/pods

# Terminate specific pod
curl -X DELETE http://localhost:8000/api/runpod/pods/{pod_id}
```

---

## Transcription Models

### Default Models

| Model | Backend | Languages | Speed |
|-------|---------|-----------|-------|
| **`parakeet-tdt-0.6b-v3`** | Parakeet (NeMo) | 25 EU languages | ~100x realtime |
| `large-v3-turbo` | Whisper | 99+ languages | ~30-40x realtime |
| `large-v3` | Whisper | 99+ languages | ~20x realtime |
| `large-v2` | Whisper | 99+ languages | ~20x realtime |
| `medium` | Whisper | 99+ languages | ~40x realtime |
| `small` | Whisper | 99+ languages | ~60x realtime |

### Managing Models

Via the Settings page or API:

```bash
# List models
curl http://localhost:8000/api/runpod/models

# Add model
curl -X POST http://localhost:8000/api/runpod/models \
  -H "Content-Type: application/json" \
  -d '{"model_id": "large-v3-turbo"}'

# Remove model
curl -X DELETE http://localhost:8000/api/runpod/models \
  -H "Content-Type: application/json" \
  -d '{"model_id": "large-v3-turbo"}'
```

---

## GPU Compatibility

!!! warning "Blocked GPUs"
    RTX 40-series consumer GPUs and NVIDIA L4 have CUDA compatibility issues with NeMo/Parakeet (`CUDA error 35`). These work fine with Whisper but fail with Parakeet.

**Working GPUs (Parakeet):**

- NVIDIA RTX A5000 (~$0.22/hr, ~87x realtime)
- NVIDIA RTX A6000
- NVIDIA RTX A4000
- NVIDIA GeForce RTX 3090
- NVIDIA L40

**Blocked GPUs (default blocklist):**

- NVIDIA GeForce RTX 4090
- NVIDIA GeForce RTX 4080
- NVIDIA L4

Blocked GPUs are automatically skipped during pod creation. To modify:

```bash
RUNPOD_BLOCKED_GPUS="NVIDIA GeForce RTX 4090,NVIDIA GeForce RTX 4080,NVIDIA L4"
```

---

## Auto-Termination

Pods automatically terminate to prevent runaway costs:

| Condition | Default | Description |
|-----------|---------|-------------|
| **Empty Queue** | ~2 min | 2 consecutive empty checks, 60s apart |
| **Idle Timeout** | 10 min | No jobs processed (safety net) |
| **Server Unreachable** | 5 min | Can't reach server |
| **Circuit Breaker** | 3 failures | Consecutive transcription failures |

### Server-Controlled Termination

When a node worker decides to terminate:

1. Worker calls `POST /api/nodes/{node_id}/request-termination`
2. Server releases claimed jobs back to queue
3. Server terminates pod via RunPod API
4. Server cleans up setup state and node registration

### Persistent/Dev Mode

To keep pods running for debugging:

```bash
# Create persistent pod
curl -X POST http://localhost:8000/api/runpod/pods \
  -H "Content-Type: application/json" \
  -d '{"persistent": true}'

# Enable on existing pod
curl -X PATCH http://localhost:8000/api/runpod/pods/{instance_id}/persistent \
  -H "Content-Type: application/json" \
  -d '{"persistent": true}'
```

---

## Pod Lifecycle

```
1. API call to create pod
   └─> Server generates instance ID
   └─> Background thread creates RunPod pod
   └─> Pod starts with Tailscale + cast2md worker

2. Pod setup
   └─> Start Tailscale (userspace networking)
   └─> Install cast2md from GitHub
   └─> GPU smoke test (Parakeet only)
   └─> Register with server as transcriber node

3. Processing
   └─> Claims jobs via standard node protocol
   └─> 3-slot prefetch queue keeps audio ready
   └─> Transcribes using Parakeet or Whisper

4. Auto-termination
   └─> Empty queue / idle / unreachable / circuit breaker
   └─> Notifies server before shutdown
   └─> Server releases jobs and cleans up
```

### GPU Smoke Test

During pod setup, a GPU smoke test runs before the worker starts (Parakeet only). It transcribes 1 second of silence to catch CUDA errors early.

- Timeout: 120 seconds
- If it fails, the pod is marked as FAILED in the admin UI

---

## Docker Image

RunPod pods use a custom Docker image with pre-installed dependencies:

| Component | Notes |
|-----------|-------|
| CUDA 12.4.1 | Runtime only |
| PyTorch 2.4.0+cu124 | Pinned for CUDA compatibility |
| NeMo toolkit | Latest version |
| Parakeet model | Pre-downloaded (~600MB) |
| faster-whisper | Fallback for Whisper models |

Image: `meltforce/cast2md-afterburner:cuda124`

Built automatically via GitHub Actions when `deploy/afterburner/Dockerfile` changes.

---

## Tailscale Networking

RunPod containers don't have `/dev/net/tun`, so Tailscale runs in **userspace mode**:

- **Inbound connections** work normally (SSH, etc.)
- **Outbound connections** use HTTP proxy on `localhost:1055`
- **HTTPS not supported** through the proxy (use HTTP -- still encrypted by Tailscale's WireGuard tunnel)
- **MagicDNS unavailable** -- `RUNPOD_SERVER_IP` is required

---

## Debugging

```bash
# SSH into pod
ssh root@<pod-tailscale-hostname>

# View worker logs
tail -100 /tmp/cast2md-node.log

# Check proxy
ss -tlnp | grep 1055

# Test server connectivity
curl -x http://localhost:1055 http://<server-ip>:8000/api/health

# Tailscale status
tailscale status
```

---

## CLI Afterburner

For running pods from the command line:

```bash
source deploy/afterburner/.env
python deploy/afterburner/afterburner.py           # Process queue
python deploy/afterburner/afterburner.py --dry-run  # Validate config
python deploy/afterburner/afterburner.py --test     # Test connectivity
python deploy/afterburner/afterburner.py --keep-alive  # Persistent mode
python deploy/afterburner/afterburner.py --terminate-all  # Stop all pods
```

Supports parallel execution:

```bash
python deploy/afterburner/afterburner.py &
python deploy/afterburner/afterburner.py &
python deploy/afterburner/afterburner.py &
```
