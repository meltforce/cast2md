# Distributed Transcription Setup Guide

This guide walks you through setting up distributed transcription with cast2md, allowing multiple machines to process transcription jobs in parallel.

## Prerequisites

### Server Requirements
- Running cast2md server (v0.1.0+)
- Network accessible to transcriber nodes (local network or Tailscale)

### Node Requirements
- Python 3.11+
- cast2md package installed
- Whisper dependencies (faster-whisper or mlx-whisper)
- Network access to the cast2md server

### Recommended Hardware for Nodes
- **Apple Silicon Mac** (M1/M2/M3/M4): Use mlx-whisper backend
- **NVIDIA GPU**: Use faster-whisper with CUDA
- **CPU-only**: Works but slower; use smaller models

## Part 1: Server Setup

### Step 1: Enable Distributed Transcription

**Option A: Via Web UI**

1. Open the cast2md web interface
2. Navigate to **Settings**
3. Find "Enable Distributed Transcription" and turn it on
4. Optionally adjust:
   - **Node Heartbeat Timeout**: Seconds before marking a node offline (default: 60)
   - **Remote Job Timeout**: Hours before reclaiming a stuck job (default: 2)
5. Click **Save Settings**

**Option B: Via Environment Variables**

Add to your `.env` file:

```bash
DISTRIBUTED_TRANSCRIPTION_ENABLED=true
NODE_HEARTBEAT_TIMEOUT_SECONDS=60
REMOTE_JOB_TIMEOUT_HOURS=2
```

Restart the server after changes.

### Step 2: Verify Server is Ready

1. Go to the **Status** page
2. You should see a "Remote Transcriber Nodes" section (may show "No nodes registered")
3. Go to the **Settings** page
4. Scroll to "Transcriber Nodes" section - this is where you'll manage nodes

### Step 3: Note Your Server URL

You'll need the server's URL for node registration. Examples:
- Local network: `http://192.168.1.100:8000`
- Tailscale: `http://your-server.tail12345.ts.net:8000`
- Docker: `http://host.docker.internal:8000` (from containers)

## Part 2: Node Setup

Perform these steps on each machine you want to use as a transcriber node.

### Quick Install (Recommended for macOS)

The easiest way to set up a node is with the guided install script:

```bash
curl -fsSL https://raw.githubusercontent.com/meltforce/cast2md/main/scripts/cast2md-node.sh | bash
```

This script:
- Checks prerequisites (Python 3.11+, Homebrew, ffmpeg)
- Handles GitHub authentication (for private repo access)
- Clones the repo to `~/.cast2md/cast2md`
- Creates a virtual environment with minimal dependencies (~280 MB vs ~600 MB full install)
- Detects Apple Silicon and installs MLX backend automatically
- Prompts for server URL and node name
- Optionally sets up as a startup service via launchd

**Updating:** Run the same command again. The script detects existing installations and updates in place.

**For private repos:** Export your GitHub token first:
```bash
export GITHUB_TOKEN=ghp_your_token_here
curl -fsSL https://raw.githubusercontent.com/meltforce/cast2md/main/scripts/cast2md-node.sh | bash
```

### Manual Install

If you prefer manual installation:

### Step 1: Install cast2md

```bash
# Clone the repository
git clone https://github.com/meltforce/cast2md.git
cd cast2md

# Create virtual environment (use Homebrew Python if system Python < 3.11)
/opt/homebrew/bin/python3 -m venv .venv
source .venv/bin/activate

# Install cast2md without dependencies
pip install --no-deps -e .

# Install node dependencies directly
pip install httpx pydantic-settings python-dotenv click fastapi \
  'uvicorn[standard]' jinja2 feedparser python-multipart

# Install transcription backend (choose one)
pip install mlx-whisper      # Apple Silicon
pip install faster-whisper   # Intel/NVIDIA
```

### Step 2: Configure Whisper Settings (Optional)

Create a `.env` file in the cast2md directory or set environment variables:

```bash
# For Apple Silicon (recommended)
WHISPER_BACKEND=mlx
WHISPER_MODEL=large-v3-turbo

# For NVIDIA GPU
WHISPER_BACKEND=faster-whisper
WHISPER_MODEL=large-v3-turbo
WHISPER_DEVICE=cuda

# For CPU
WHISPER_BACKEND=faster-whisper
WHISPER_MODEL=base
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
```

### Step 3: Register the Node

```bash
cast2md node register \
  --server http://YOUR_SERVER:8000 \
  --name "M4 MacBook Pro"
```

Replace:
- `YOUR_SERVER` with your server's IP or hostname
- `"M4 MacBook Pro"` with a descriptive name for this node

**Example output:**
```
Registering with server: http://192.168.1.100:8000
Registered successfully!
  Node ID: a1b2c3d4...
  Config saved to: /Users/you/.cast2md/node.json

Start the node with: cast2md node start
```

The credentials are stored in `~/.cast2md/node.json`:
```json
{
  "server_url": "http://192.168.1.100:8000",
  "node_id": "a1b2c3d4-e5f6-...",
  "api_key": "generated-secret-key",
  "name": "M4 MacBook Pro"
}
```

### Step 4: Start the Node Worker

```bash
cast2md node start
```

**Example output:**
```
Starting transcriber node 'M4 MacBook Pro'
Server: http://192.168.1.100:8000
Status UI: http://localhost:8001
Press Ctrl+C to stop
2024-01-15 10:30:00 - cast2md.node.worker - INFO - Started heartbeat thread
2024-01-15 10:30:00 - cast2md.node.worker - INFO - Started job poll thread
2024-01-15 10:30:00 - cast2md.node.worker - INFO - Node 'M4 MacBook Pro' started, polling http://192.168.1.100:8000
```

The node will:
- **Auto-open browser** to the status UI at http://localhost:8001
- Send heartbeats every 30 seconds
- Poll for jobs every 5 seconds
- Process any available transcription jobs

### Node Web UI

The node runs a local web interface on port 8001 with four pages:

**Status Page** (`/`)
- Node configuration (ID, server URL)
- Worker status (Running/Stopped)
- Current job with progress bar and elapsed time

**Queue Page** (`/queue`)
- Transcription queue stats from main server (queued, running, completed, failed)
- List of running and queued jobs
- Auto-refreshes every 10 seconds

**Settings Page** (`/settings`)
- System information (platform, CPU cores, memory)
- Whisper configuration (model, backend, device, compute type)
- Node configuration details

**Server Link**
- Opens the main cast2md server in a new tab

### Step 5: Verify Node is Connected

**On the node:**
```bash
cast2md node status
```

**Example output:**
```
Node Configuration
========================================
Name: M4 MacBook Pro
Node ID: a1b2c3d4-e5f6-...
Server: http://192.168.1.100:8000
Config: /Users/you/.cast2md/node.json

Server Connection
----------------------------------------
Status: Connected
```

**On the server:**
1. Go to **Status** page - you should see the node listed under "Remote Transcriber Nodes"
2. Go to **Settings** page - the node appears in the "Transcriber Nodes" table

## Part 3: Running Transcription Jobs

### Queue Episodes for Transcription

Use any of the normal methods to queue episodes:

1. **Web UI**: Click "Queue Transcription" on an episode
2. **CLI**: `cast2md process <episode_id>`
3. **Batch**: Click "Queue All Pending" on the Status page

### Watch the Distributed Processing

When jobs are queued:

1. **Local worker** picks up the first unclaimed job
2. **Remote nodes** poll and claim other jobs
3. Both process in parallel

**On the server Status page:**
- **Workers section**: Shows local transcription workers and their current jobs
- **Remote Transcriber Nodes section**: Shows each node's status (online/busy/offline), current episode being transcribed with elapsed time, and last heartbeat (e.g., "5s ago")
- **Currently Processing table**: Lists all active jobs with a "Worker" column showing either "Local" or the node name (e.g., "M4 MacBook")

**On the node console:**
```
2024-01-15 10:35:00 - INFO - Claimed job 42: Episode Title Here
2024-01-15 10:35:00 - INFO - Downloading audio from /api/nodes/jobs/42/audio
2024-01-15 10:35:05 - INFO - Downloaded to /tmp/.../audio.mp3 (45000000 bytes)
2024-01-15 10:35:05 - INFO - Transcribing /tmp/.../audio.mp3
2024-01-15 10:45:00 - INFO - Transcription complete (125000 chars)
2024-01-15 10:45:01 - INFO - Job 42 completed successfully
```

## Part 4: Managing Nodes

### View All Nodes

**Web UI:** Settings page → Transcriber Nodes section

**API:**
```bash
curl http://localhost:8000/api/nodes
```

### Test Node Connectivity

**Web UI:** Click "Test" next to a node in Settings

**API:**
```bash
curl -X POST http://localhost:8000/api/nodes/{node_id}/test
```

### Remove a Node

**Web UI:** Click "Delete" next to a node in Settings

**CLI (on the node):**
```bash
cast2md node unregister
```

**API:**
```bash
curl -X DELETE http://localhost:8000/api/nodes/{node_id}
```

### Add Node Manually (Without CLI Registration)

If you can't run `cast2md node register` on the node machine, you can add it manually via the web UI:

1. Go to Settings → Transcriber Nodes
2. Expand "Add Node Manually"
3. Fill in:
   - **Name**: Descriptive name
   - **URL**: Node's URL (for connectivity tests)
   - **Whisper Model**: Optional
   - **Priority**: Lower = preferred (default: 10)
4. Click "Add Node"
5. **Save the API key shown** - you'll need it for the node

Then on the node, manually create `~/.cast2md/node.json`:
```json
{
  "server_url": "http://server:8000",
  "node_id": "the-uuid-shown",
  "api_key": "the-api-key-shown",
  "name": "Node Name"
}
```

## Part 5: Running as a Service

### macOS (launchd)

Create `~/Library/LaunchAgents/com.cast2md.node.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.cast2md.node</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/venv/bin/cast2md</string>
        <string>node</string>
        <string>start</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/cast2md</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/cast2md-node.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/cast2md-node.log</string>
</dict>
</plist>
```

Load the service:
```bash
launchctl load ~/Library/LaunchAgents/com.cast2md.node.plist
```

### Linux (systemd)

Create `/etc/systemd/system/cast2md-node.service`:

```ini
[Unit]
Description=cast2md Transcriber Node
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/path/to/cast2md
ExecStart=/path/to/venv/bin/cast2md node start
Restart=always
RestartSec=10
Environment=WHISPER_MODEL=large-v3-turbo
Environment=WHISPER_BACKEND=faster-whisper

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable cast2md-node
sudo systemctl start cast2md-node
```

### Docker

Create a `Dockerfile.node`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .
RUN pip install -e .

# Copy node config (or mount as volume)
COPY node.json /root/.cast2md/node.json

CMD ["cast2md", "node", "start"]
```

Run:
```bash
docker build -f Dockerfile.node -t cast2md-node .
docker run -d --name cast2md-node \
  -v ~/.cast2md:/root/.cast2md \
  -e WHISPER_MODEL=base \
  cast2md-node
```

## Troubleshooting

### Node Shows "Offline" on Server

1. **Check network connectivity:**
   ```bash
   curl http://YOUR_SERVER:8000/api/system/health
   ```

2. **Verify node is running:**
   ```bash
   cast2md node status
   ```

3. **Check logs for errors** - look for connection refused, timeouts, or auth errors

4. **Verify API key is correct** - re-register if needed:
   ```bash
   cast2md node unregister
   cast2md node register --server http://... --name "..."
   ```

### Jobs Not Being Claimed by Node

1. **Ensure distributed transcription is enabled** on the server
2. **Check node status** - must be "online" or "busy"
3. **Verify jobs exist:**
   ```bash
   curl http://localhost:8000/api/queue/status
   ```
4. **Check job type** - only transcription jobs go to nodes, not downloads

### Transcription Fails on Node

1. **Check Whisper is working locally:**
   ```bash
   cast2md transcribe <episode_id>
   ```

2. **Verify model is downloaded:**
   - For mlx: Models auto-download from HuggingFace
   - For faster-whisper: First run downloads the model

3. **Check available memory/GPU memory** - large models need significant resources

### Job Stuck on Node

If a job shows as "running" but the node isn't processing:

1. **Reset the job** via API:
   ```bash
   curl -X POST http://localhost:8000/api/queue/{job_id}/reset
   ```
   This resets the job to "queued" and clears the node assignment.

2. **Wait for timeout** - jobs auto-reclaim after 2 hours (configurable)

3. **Check node logs** for errors

4. **Verify node status** - if the node was restarted, it may have lost its job context. The reset endpoint now properly clears both the job status and node assignment.

### Server Can't Reach Node (Test Fails)

This is expected if the node is behind NAT. The "Test" feature is optional - nodes work fine without server → node connectivity since nodes pull work from the server.

## Performance Tips

### Optimal Node Configuration

**Apple Silicon (M1/M2/M3/M4):**
```bash
WHISPER_BACKEND=mlx
WHISPER_MODEL=large-v3-turbo  # Best speed/quality balance
```

**NVIDIA GPU (8GB+ VRAM):**
```bash
WHISPER_BACKEND=faster-whisper
WHISPER_MODEL=large-v3-turbo
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
```

**NVIDIA GPU (4-6GB VRAM):**
```bash
WHISPER_BACKEND=faster-whisper
WHISPER_MODEL=medium
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=int8
```

### Scaling Recommendations

| Episodes/Day | Recommended Setup |
|--------------|-------------------|
| < 10 | Server only, no nodes needed |
| 10-50 | 1-2 fast nodes (M4 Mac or GPU) |
| 50-200 | 3-5 nodes |
| 200+ | Consider dedicated GPU servers |

### Network Considerations

- **Local Network**: Best performance, lowest latency
- **Tailscale/WireGuard**: Good for remote nodes, adds ~10-50ms latency
- **Public Internet**: Works but not recommended (security, latency)

Audio files can be large (50-200MB per episode), so bandwidth matters for the download step. Transcription happens locally on the node.
