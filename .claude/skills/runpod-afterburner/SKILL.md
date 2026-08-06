---
name: runpod-afterburner
description: On-demand RunPod GPU transcription workers for cast2md — pod lifecycle, Tailscale userspace networking, auto-termination, GPU compatibility, and the server-side RunPod API. Use when working on deploy/afterburner/, services/runpod_service.py, api/runpod.py, node/worker.py, or when debugging pods that fail to start, terminate early, or hit CUDA errors.
---

# RunPod Afterburner

On-demand GPU transcription worker for processing large backlogs. Uses **Parakeet TDT 0.6B v3** by default for fast transcription (supports 25 European languages including German).

## Quick Start

```bash
source deploy/afterburner/.env
python deploy/afterburner/afterburner.py --dry-run  # Validate config
python deploy/afterburner/afterburner.py --test     # Test connectivity
python deploy/afterburner/afterburner.py            # Process queue
```

## Docker Image

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

**Building**: The `build-afterburner` job in `.forgejo/workflows/ci.yml` builds the image when `deploy/afterburner/Dockerfile` changes on `main`, or on `workflow_dispatch`. It pushes to Docker Hub with `--no-cache`. See `deploy/afterburner/IMAGE.md` for manual build instructions.

## GPU Validation

During pod setup, a GPU smoke test runs before the worker starts (Parakeet only). It transcribes 1 second of silence to catch CUDA errors early, preventing a broken GPU from burning through the job queue.

- Runs between "Installing cast2md" and "Registering node" setup steps
- Timeout: 120 seconds (model is pre-loaded in image)
- If it fails, the pod is marked as FAILED in the admin UI
- Combined with the circuit breaker (see Auto-Termination), this provides defense in depth

## Transcription Models

RunPod pods default to Parakeet but can use Whisper models. Models are configurable via the RunPod settings page:

- **Manage Models**: Add/remove models in "Manage Transcription Models" section
- **Custom Models**: Add any Whisper or Parakeet model by ID
- **API**: `GET/POST/DELETE /api/runpod/models`

Default models:
- `parakeet-tdt-0.6b-v3` - Fast, 25 EU languages (default)
- `large-v3-turbo`, `large-v3`, `large-v2`, `medium`, `small` - Whisper models

## Node Worker Prefetch

The node worker uses a **3-slot prefetch queue** to keep audio ready for instant transcription. This is important for Parakeet which transcribes faster than download speed.

## Job State Synchronization

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

## Auto-Termination

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

`POST /api/nodes/{node_id}/request-termination` requires the `X-Transcriber-Key` header (node's API key) and returns `{"status": "ignored", "terminated": false}` for non-RunPod nodes. Jobs are released before termination to prevent orphaned work.

**Automatic Cleanup**: Orphaned RunPod nodes are cleaned up automatically:
- On server startup (`main.py:lifespan()`)
- Manual trigger: `POST /api/runpod/nodes/cleanup-orphaned`
- Catches pods that crashed or terminated without notifying server

## Pod Setup Architecture

There are two ways to create RunPod pods:

1. **Server-side** (`runpod_service.py`): Pods self-setup via a startup script that calls back to the server's `/api/runpod/pods/{id}/setup-progress` endpoint. No SSH or Tailscale CLI needed on the server.
2. **CLI** (`deploy/afterburner/afterburner.py`): Uses SSH from the local machine to set up pods. Requires Tailscale on the local machine.

Both paths result in the same pod configuration. The server-side path was introduced because the server runs in Docker (no Tailscale CLI access).

## Tailscale Userspace Networking

RunPod containers don't have `/dev/net/tun`, so Tailscale must run in **userspace mode**. This applies to both setup paths and has significant implications:

### 1. No TUN Interface

```bash
# This is required - can't use default TUN mode
tailscaled --tun=userspace-networking --state=/var/lib/tailscale/tailscaled.state
```

### 2. Inbound Connections Work Normally

Tailscale SSH works fine because `tailscaled` handles incoming connections:

```bash
# This works once pod is on Tailnet
ssh root@<pod-hostname>
```

### 3. Outbound Connections Need HTTP Proxy

Applications can't directly connect to Tailscale IPs. Must use the HTTP proxy:

```bash
# Start tailscaled WITH the proxy
tailscaled --tun=userspace-networking --outbound-http-proxy-listen=localhost:1055 &

# Use proxy for outbound traffic
curl -x http://localhost:1055 http://100.x.x.x:8000/api/health

# Or set environment variable
http_proxy=http://localhost:1055 some-command
```

### 4. HTTP Proxy Doesn't Support HTTPS CONNECT

**Critical limitation**: The proxy only handles plain HTTP. HTTPS fails:

```bash
# Works (HTTP)
curl -x http://localhost:1055 http://server:8000/api/health

# Fails (HTTPS - no CONNECT tunneling)
curl -x http://localhost:1055 https://server/api/health
```

**Solution**: Use HTTP on port 8000 for internal Tailscale traffic. It's still encrypted by Tailscale's WireGuard tunnel.

This limitation is also why `services/pod_setup.py` and `deploy/afterburner/afterburner.py` install cast2md from GitHub rather than Forgejo — see "GitHub references that must stay" in the root `CLAUDE.md`.

### 5. MagicDNS Not Available

`*.ts.net` hostnames don't resolve in userspace mode. Must use `/etc/hosts`:

```bash
echo '100.x.x.x server.tailnet.ts.net' >> /etc/hosts
```

This is why `CAST2MD_SERVER_IP` environment variable is required.

### 6. Pod Detection with Multiple Orphaned Hosts (CLI only)

Tailscale keeps offline hosts visible. When hostname is taken, it adds `-1`, `-2` suffixes. The CLI afterburner handles this by:

1. Matching hostname prefix (`runpod-afterburner*`)
2. Filtering for `Online=true`
3. Sorting by `Created` timestamp (newest first)
4. Verifying SSH connectivity before proceeding

The server-side path does not use Tailscale peer detection — it uses the pod self-setup HTTP callback instead.

## Parallel Execution

The CLI supports parallel execution. Run multiple instances simultaneously:

```bash
# Each generates unique instance ID (e.g., "a3f2")
python deploy/afterburner/afterburner.py &
python deploy/afterburner/afterburner.py &
python deploy/afterburner/afterburner.py &

# Terminate all
python deploy/afterburner/afterburner.py --terminate-all
```

## Debugging Tips (via Tailscale SSH)

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

## Server-Side RunPod Management

The server includes a RunPod service for managing GPU workers via API. This enables future admin UI integration.

### Enabling Server-Side RunPod

1. Install optional dependency: `pip install cast2md[runpod]`
2. Set environment variables:
   ```bash
   RUNPOD_API_KEY=...           # Required
   RUNPOD_SERVER_URL=https://<your-tailnet>
   RUNPOD_SERVER_IP=100.x.x.x   # Tailscale IP
   ```
3. Ensure a RunPod Secret named `ts_auth_key` exists in your RunPod account (used by pods for Tailscale auth)
4. Enable in settings: `runpod_enabled=true`

### Dev Mode

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

### Settings

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

### GPU Compatibility

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
