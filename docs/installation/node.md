# Transcriber Node Installation

Install cast2md as a remote transcription worker that connects to a running cast2md server.

!!! note
    This page covers node installation only. For the full distributed transcription setup including server configuration, see the [Setup Guide](../distributed/setup.md).

## Quick Install (Recommended)

The install script handles everything automatically:

```bash
curl -fsSL https://raw.githubusercontent.com/meltforce/cast2md/main/scripts/cast2md-node.sh | bash
```

The script:

- Supports **macOS** (launchd) and **Linux** (systemd)
- Checks prerequisites (Python 3.11+, ffmpeg)
- Creates a virtual environment with minimal dependencies (~280 MB)
- Detects Apple Silicon and installs MLX backend automatically
- Prompts for server URL and node name
- Offers auto-start service, shell script, or manual operation

**Updating:** Run the same command again to update an existing installation.

**Uninstalling:** Run the script and choose the uninstall option.

## Manual Install

### 1. Clone and Install

```bash
git clone https://github.com/meltforce/cast2md.git
cd cast2md

python3 -m venv .venv
source .venv/bin/activate

# Install cast2md without full dependencies
pip install --no-deps -e .

# Install node-only dependencies
pip install httpx pydantic-settings python-dotenv click fastapi \
  'uvicorn[standard]' jinja2 python-multipart
```

### 2. Install Transcription Backend

=== "Apple Silicon"

    ```bash
    pip install mlx-whisper
    ```

=== "NVIDIA GPU"

    ```bash
    pip install faster-whisper
    ```

=== "CPU"

    ```bash
    pip install faster-whisper
    ```

### 3. Configure (Optional)

Create a `.env` file:

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

### 4. Register with Server

```bash
cast2md node register \
  --server http://YOUR_SERVER:8000 \
  --name "My Transcriber Node"
```

Credentials are saved to `~/.cast2md/node.json`.

### 5. Start the Node

```bash
cast2md node start
```

The node opens a local status UI at `http://localhost:8001` and begins polling the server for jobs.

## Verify Connection

```bash
cast2md node status
```

Check the server's Status page to confirm the node appears under "Remote Transcriber Nodes".

## Next Steps

- [Full Setup Guide](../distributed/setup.md) -- server setup, managing nodes, running as a service
- [Whisper Models](../configuration/whisper-models.md) -- choose the right model for your hardware
