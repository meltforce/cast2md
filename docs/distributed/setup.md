# Distributed Transcription Setup

This guide walks through setting up distributed transcription with cast2md, allowing multiple machines to process transcription jobs in parallel.

## Prerequisites

### Server Requirements

- Running cast2md server
- Network accessible to transcriber nodes (local network or Tailscale)

### Node Requirements

- Python 3.11+
- cast2md package installed
- Whisper dependencies (faster-whisper or mlx-whisper)
- Network access to the cast2md server

### Recommended Hardware

| Platform | Backend | Notes |
|----------|---------|-------|
| **Apple Silicon Mac** (M1-M4) | mlx-whisper | Best power efficiency |
| **NVIDIA GPU** (8GB+ VRAM) | faster-whisper + CUDA | Highest throughput |
| **CPU-only** | faster-whisper | Works but slower, use smaller models |

---

## Part 1: Server Setup

### Enable Distributed Transcription

=== "Web UI"

    1. Open the cast2md web interface
    2. Navigate to **Settings**
    3. Enable "Distributed Transcription"
    4. Optionally adjust:
        - **Node Heartbeat Timeout**: seconds before marking a node offline (default: 60)
        - **Remote Job Timeout**: minutes before reclaiming a stuck job (default: 30)
    5. Click **Save Settings**

=== "Environment Variables"

    Add to your `.env` file:

    ```bash
    DISTRIBUTED_TRANSCRIPTION_ENABLED=true
    NODE_HEARTBEAT_TIMEOUT_SECONDS=60
    REMOTE_JOB_TIMEOUT_MINUTES=30
    ```

    Restart the server after changes.

### Verify Server is Ready

1. Go to the **Status** page
2. You should see a "Remote Transcriber Nodes" section (may show "No nodes registered")
3. The **Settings** page has a "Transcriber Nodes" section for managing nodes

### Note Your Server URL

You'll need the server's URL for node registration:

- Local network: `http://192.168.1.100:8000`
- Tailscale: `http://your-server.tail12345.ts.net:8000`
- Docker: `http://host.docker.internal:8000` (from containers)

---

## Part 2: Node Setup

Perform these steps on each machine you want to use as a transcriber node.

### Quick Install (Recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/meltforce/cast2md/main/scripts/cast2md-node.sh | bash
```

This script:

- Supports **macOS** (launchd) and **Linux** (systemd)
- Checks prerequisites (Python 3.11+, ffmpeg)
- Creates a virtual environment with minimal dependencies (~280 MB)
- Detects Apple Silicon and installs MLX backend automatically
- Prompts for server URL and node name
- Offers three service options: auto-start service, shell script, or manual

!!! tip
    **Updating:** Run the same command again. The script detects existing installations and offers to update.

    **Uninstalling:** Run the script and choose the uninstall option.

### Manual Install

#### Step 1: Install cast2md

```bash
git clone https://github.com/meltforce/cast2md.git
cd cast2md

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install cast2md without dependencies
pip install --no-deps -e .

# Install node dependencies
pip install httpx pydantic-settings python-dotenv click fastapi \
  'uvicorn[standard]' jinja2 python-multipart

# Install transcription backend (choose one)
pip install mlx-whisper      # Apple Silicon
pip install faster-whisper   # Intel/NVIDIA
```

#### Step 2: Configure Whisper Settings (Optional)

Create a `.env` file or set environment variables:

=== "Apple Silicon"

    ```bash
    WHISPER_BACKEND=mlx
    WHISPER_MODEL=large-v3-turbo
    ```

=== "NVIDIA GPU"

    ```bash
    WHISPER_BACKEND=faster-whisper
    WHISPER_MODEL=large-v3-turbo
    WHISPER_DEVICE=cuda
    WHISPER_COMPUTE_TYPE=float16
    ```

=== "CPU"

    ```bash
    WHISPER_BACKEND=faster-whisper
    WHISPER_MODEL=base
    WHISPER_DEVICE=cpu
    WHISPER_COMPUTE_TYPE=int8
    ```

#### Step 3: Register the Node

```bash
cast2md node register \
  --server http://YOUR_SERVER:8000 \
  --name "M4 MacBook Pro"
```

Credentials are stored in `~/.cast2md/node.json`:

```json
{
  "server_url": "http://192.168.1.100:8000",
  "node_id": "a1b2c3d4-e5f6-...",
  "api_key": "generated-secret-key",
  "name": "M4 MacBook Pro"
}
```

#### Step 4: Start the Node Worker

```bash
cast2md node start
```

The node will:

- **Auto-open browser** to the status UI at `http://localhost:8001`
- Send heartbeats every 30 seconds
- Poll for jobs every 5 seconds
- Process any available transcription jobs

#### Step 5: Verify Connection

```bash
cast2md node status
```

On the server, check the **Status** page -- the node should appear under "Remote Transcriber Nodes".

---

## Node Web UI

The node runs a local web interface on port 8001:

| Page | Description |
|------|-------------|
| **Status** (`/`) | Node config, worker status, current job with progress bar |
| **Queue** (`/queue`) | Transcription queue stats, running and queued jobs |
| **Settings** (`/settings`) | System info, Whisper config, node config |
| **Server Link** | Opens the main cast2md server |

---

## Part 3: Running Transcription Jobs

Queue episodes using any method:

1. **Web UI**: Click "Get Transcript" or "Download Audio" on an episode
2. **CLI**: `cast2md process <episode_id>`
3. **Batch**: Click "Get All Transcripts" on a feed page

When jobs are queued:

1. **Local worker** picks up the first unclaimed job
2. **Remote nodes** poll and claim other jobs
3. Both process in parallel

---

## Part 4: Managing Nodes

### View All Nodes

- **Web UI:** Settings page -> Transcriber Nodes section
- **API:** `GET /api/nodes`

### Test Node Connectivity

- **Web UI:** Click "Test" next to a node in Settings
- **API:** `POST /api/nodes/{node_id}/test`

### Remove a Node

- **Web UI:** Click "Delete" next to a node in Settings
- **CLI (on the node):** `cast2md node unregister`
- **API:** `DELETE /api/nodes/{node_id}`

### Rename a Node

1. Edit `~/.cast2md/node.json` and change the `"name"` field
2. Restart the node
3. The server sees the new name within 30 seconds (synced via heartbeat)

### Add Node Manually (Without CLI)

1. Go to Settings -> Transcriber Nodes -> "Add Node Manually"
2. Fill in name, URL, optional whisper model and priority
3. Click "Add Node"
4. **Save the API key shown** -- you'll need it for the node

Then create `~/.cast2md/node.json` manually on the node machine.

---

## Part 5: Running as a Service

=== "macOS (launchd)"

    If installed via script, use the helper commands:

    ```bash
    ~/.cast2md/stop      # Stop the node
    ~/.cast2md/start     # Start the node
    ~/.cast2md/restart   # Restart the node
    ~/.cast2md/logs      # Follow the log file
    ```

    For manual setup, create `~/Library/LaunchAgents/com.cast2md.node.plist`:

    ```xml
    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
      "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
        <key>Label</key>
        <string>com.cast2md.node</string>
        <key>ProgramArguments</key>
        <array>
            <string>/path/to/venv/bin/cast2md</string>
            <string>node</string>
            <string>start</string>
            <string>--no-browser</string>
        </array>
        <key>WorkingDirectory</key>
        <string>/path/to/cast2md</string>
        <key>RunAtLoad</key>
        <true/>
        <key>KeepAlive</key>
        <true/>
        <key>StandardOutPath</key>
        <string>~/.cast2md/node.log</string>
        <key>StandardErrorPath</key>
        <string>~/.cast2md/node.log</string>
    </dict>
    </plist>
    ```

    Load:

    ```bash
    launchctl load ~/Library/LaunchAgents/com.cast2md.node.plist
    ```

=== "Linux (systemd)"

    If installed via script:

    ```bash
    systemctl --user stop cast2md-node
    systemctl --user start cast2md-node
    systemctl --user restart cast2md-node
    systemctl --user status cast2md-node
    ```

    Log file: `~/.cast2md/node.log`

    For manual setup, create `~/.config/systemd/user/cast2md-node.service`:

    ```ini
    [Unit]
    Description=cast2md Transcriber Node
    After=network.target

    [Service]
    Type=simple
    WorkingDirectory=/path/to/cast2md
    ExecStart=/path/to/venv/bin/cast2md node start --no-browser
    Restart=always
    RestartSec=10
    Environment=WHISPER_MODEL=large-v3-turbo
    Environment=WHISPER_BACKEND=faster-whisper

    [Install]
    WantedBy=default.target
    ```

    Enable and start:

    ```bash
    systemctl --user daemon-reload
    systemctl --user enable cast2md-node
    systemctl --user start cast2md-node
    ```

=== "Docker"

    ```dockerfile
    FROM python:3.11-slim
    WORKDIR /app
    COPY . .
    RUN pip install -e .
    COPY node.json /root/.cast2md/node.json
    CMD ["cast2md", "node", "start"]
    ```

    ```bash
    docker build -f Dockerfile.node -t cast2md-node .
    docker run -d --name cast2md-node \
      -v ~/.cast2md:/root/.cast2md \
      -e WHISPER_MODEL=base \
      cast2md-node
    ```

---

## Auto-Termination

Node workers automatically terminate to save costs when idle. This is especially important for paid cloud instances like RunPod.

### Termination Conditions

| Condition | Default | Description |
|-----------|---------|-------------|
| **Empty Queue** | 2 checks x 60s | No claimable jobs available |
| **Idle Timeout** | 10 minutes | No jobs processed (safety net) |
| **Server Unreachable** | 5 minutes | Can't reach server |
| **Circuit Breaker** | 3 consecutive failures | Broken GPU protection |

### Configuration

```bash
NODE_REQUIRED_EMPTY_CHECKS=2
NODE_EMPTY_QUEUE_WAIT=60
NODE_IDLE_TIMEOUT_MINUTES=10
NODE_SERVER_UNREACHABLE_MINUTES=5
NODE_MAX_CONSECUTIVE_FAILURES=3

# Disable ALL auto-termination
NODE_PERSISTENT=1
```

---

## Troubleshooting

### Node Shows "Offline" on Server

1. Check network connectivity: `curl http://YOUR_SERVER:8000/api/system/health`
2. Verify node is running: `cast2md node status`
3. Check logs for connection errors
4. Re-register if needed: `cast2md node unregister && cast2md node register --server ... --name ...`

### Jobs Not Being Claimed

1. Ensure distributed transcription is enabled on the server
2. Check node status -- must be "online" or "busy"
3. Verify jobs exist: `curl http://localhost:8000/api/queue/status`
4. Only transcription jobs go to nodes, not downloads

### Transcription Fails on Node

1. Test Whisper locally: `cast2md transcribe <episode_id>`
2. Verify model is downloaded (auto-downloads on first use)
3. Check available memory -- large models need significant resources

### Job Stuck on Node

1. Reset via API: `curl -X POST http://localhost:8000/api/queue/{job_id}/reset`
2. Wait for timeout -- jobs auto-reclaim after the configured timeout
3. Check node logs for errors

---

## Performance Tips

=== "Apple Silicon"

    ```bash
    WHISPER_BACKEND=mlx
    WHISPER_MODEL=large-v3-turbo
    ```

=== "NVIDIA GPU (8GB+ VRAM)"

    ```bash
    WHISPER_BACKEND=faster-whisper
    WHISPER_MODEL=large-v3-turbo
    WHISPER_DEVICE=cuda
    WHISPER_COMPUTE_TYPE=float16
    ```

=== "NVIDIA GPU (4-6GB VRAM)"

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
| 200+ | Consider RunPod GPU workers |

### Network Considerations

- **Local Network**: Best performance, lowest latency
- **Tailscale/WireGuard**: Good for remote nodes, adds ~10-50ms latency
- **Public Internet**: Not recommended (security, latency)

Audio files can be 50-200MB per episode, so bandwidth matters for the download step. Transcription happens locally on the node.
