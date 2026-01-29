# Environment Variables

Complete reference for all cast2md configuration options.

All settings use uppercase environment variable names. They can be set in a `.env` file or as system environment variables.

## Storage

| Variable | Default | Description |
|----------|---------|-------------|
| `STORAGE_PATH` | `./data/podcasts` | Base directory for audio and transcripts |
| `TEMP_DOWNLOAD_PATH` | `./data/temp` | Temporary directory for downloads in progress |

## Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | *(required)* | PostgreSQL connection string |
| `POSTGRES_PASSWORD` | *(required for Docker)* | Password for Docker Compose PostgreSQL |

## Whisper Transcription

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_MODEL` | `large-v3-turbo` | Model identifier (see [Whisper Models](whisper-models.md)) |
| `WHISPER_DEVICE` | `auto` | Device: `cpu`, `cuda`, or `auto` |
| `WHISPER_COMPUTE_TYPE` | `int8` | Precision: `int8`, `float16`, `float32` |
| `WHISPER_BACKEND` | `auto` | Backend: `auto`, `faster-whisper`, `mlx` |
| `WHISPER_CHUNK_THRESHOLD_MINUTES` | `30` | Episodes longer than this are chunked |
| `WHISPER_CHUNK_SIZE_MINUTES` | `30` | Size of each chunk |
| `TRANSCRIPTION_BACKEND` | `whisper` | Primary backend: `whisper` or `parakeet` |

## Downloads

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_CONCURRENT_DOWNLOADS` | `2` | Maximum simultaneous audio downloads |
| `MAX_TRANSCRIPT_DOWNLOAD_WORKERS` | `4` | Parallel workers for external transcripts |
| `MAX_RETRY_ATTEMPTS` | `3` | Maximum retries for failed downloads |
| `REQUEST_TIMEOUT` | `30` | HTTP request timeout in seconds |

## Job Queue

| Variable | Default | Description |
|----------|---------|-------------|
| `STUCK_THRESHOLD_MINUTES` | `30` | Jobs running longer than this are marked stuck |

## Transcript Discovery

| Variable | Default | Description |
|----------|---------|-------------|
| `TRANSCRIPT_UNAVAILABLE_AGE_DAYS` | `14` | Episodes older than this without external transcript URLs are marked `needs_audio` |
| `TRANSCRIPT_RETRY_DAYS` | `14` | How long to retry external transcript downloads |

## iTunes

| Variable | Default | Description |
|----------|---------|-------------|
| `ITUNES_COUNTRY` | `de` | ISO 3166-1 alpha-2 country code for iTunes lookups |

## Notifications (ntfy)

| Variable | Default | Description |
|----------|---------|-------------|
| `NTFY_ENABLED` | `false` | Enable ntfy.sh notifications |
| `NTFY_URL` | `https://ntfy.sh` | ntfy service URL |
| `NTFY_TOPIC` | *(empty)* | ntfy topic name (required if enabled) |

## Distributed Transcription

| Variable | Default | Description |
|----------|---------|-------------|
| `DISTRIBUTED_TRANSCRIPTION_ENABLED` | `false` | Enable remote transcription nodes |
| `NODE_HEARTBEAT_TIMEOUT_SECONDS` | `60` | Seconds before marking a node offline |
| `REMOTE_JOB_TIMEOUT_MINUTES` | `30` | Maximum time for remote transcription jobs |

## RunPod GPU Workers

### Credentials (Environment-Only)

These are never stored in the database:

| Variable | Default | Description |
|----------|---------|-------------|
| `RUNPOD_API_KEY` | *(empty)* | RunPod API key |
| `RUNPOD_TS_AUTH_KEY` | *(empty)* | Tailscale auth key for pod networking |

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `RUNPOD_ENABLED` | `false` | Enable RunPod GPU workers |
| `RUNPOD_MAX_PODS` | `3` | Maximum concurrent GPU pods |
| `RUNPOD_AUTO_SCALE` | `false` | Auto-start pods on queue growth |
| `RUNPOD_SCALE_THRESHOLD` | `10` | Queue depth to trigger auto-scaling |
| `RUNPOD_PODS_PER_THRESHOLD` | `1` | Pods to start per threshold crossed |
| `RUNPOD_IDLE_TIMEOUT_MINUTES` | `10` | Auto-terminate idle pods (0 to disable) |
| `RUNPOD_GPU_TYPE` | `NVIDIA RTX A5000` | Preferred GPU type |
| `RUNPOD_BLOCKED_GPUS` | `NVIDIA GeForce RTX 4090,...` | Comma-separated GPU blocklist |
| `RUNPOD_WHISPER_MODEL` | `parakeet-tdt-0.6b-v3` | Default model for pods |
| `RUNPOD_IMAGE_NAME` | `meltforce/cast2md-afterburner:cuda124` | Docker image for pods |
| `RUNPOD_TS_HOSTNAME` | `runpod-afterburner` | Base Tailscale hostname |
| `RUNPOD_GITHUB_REPO` | `meltforce/cast2md` | GitHub repo for pod code updates |
| `RUNPOD_SERVER_URL` | *(empty)* | Server URL for pod registration |
| `RUNPOD_SERVER_IP` | *(empty)* | Tailscale IP of server |

## Node Worker Auto-Termination

These apply to transcriber node workers (including RunPod pods):

| Variable | Default | Description |
|----------|---------|-------------|
| `NODE_REQUIRED_EMPTY_CHECKS` | `2` | Empty queue checks before termination |
| `NODE_EMPTY_QUEUE_WAIT` | `60` | Seconds between empty queue checks |
| `NODE_IDLE_TIMEOUT_MINUTES` | `10` | Terminate after no jobs processed (0 to disable) |
| `NODE_SERVER_UNREACHABLE_MINUTES` | `5` | Terminate after server unreachable |
| `NODE_MAX_CONSECUTIVE_FAILURES` | `3` | Terminate after consecutive failures (0 to disable) |
| `NODE_PERSISTENT` | `0` | Set to `1` to disable all auto-termination |

## Server

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8000` | HTTP port (Docker) |
| `DATA_PATH` | `./data` | Data directory (Docker) |
| `VERSION` | `latest` | Docker image version tag |

## Tailscale (Docker Sidecar)

| Variable | Default | Description |
|----------|---------|-------------|
| `TS_AUTHKEY` | *(empty)* | Tailscale auth key |
| `TAILSCALE_HOSTNAME` | `cast2md` | Tailscale hostname |
