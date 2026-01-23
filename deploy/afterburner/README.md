# RunPod Afterburner

On-demand GPU transcription worker that spins up a RunPod pod, processes the transcription backlog, and auto-terminates when complete.

## Architecture

```
Local Machine                    RunPod Pod                     cast2md Server
┌─────────────┐                 ┌─────────────┐                ┌─────────────┐
│             │  1. Create pod  │             │                │             │
│ afterburner ├────────────────►│  GPU Worker │                │   Server    │
│    .py      │  (from template)│             │                │             │
│             │                 │  2. Startup │  3. Tailscale  │  (Tailscale │
│             │                 │     script  ├───────────────►│    only)    │
│             │                 │   (auto)    │     connect    │             │
│             │                 │             │                │             │
│             │  5. Terminate   │  4. Process │  Poll jobs     │             │
│             │◄────────────────┤     jobs    │◄──────────────►│             │
└─────────────┘   when done     └─────────────┘                └─────────────┘
```

The script uses a **template-based approach**:
1. Creates/updates a RunPod template with a startup script
2. Creates pod from template - startup script runs automatically
3. Startup script installs Tailscale, cast2md, and starts the worker
4. Waits for pod to appear on Tailscale
5. Monitors queue and terminates when empty

## Prerequisites

### 1. RunPod Account

1. Create account at [runpod.io](https://runpod.io)
2. Add credits (~$15 for 40+ hours of RTX 4090 time)
3. Generate API key: Settings → API Keys
4. Store as environment variable:
   ```bash
   export RUNPOD_API_KEY="your-api-key"
   ```

### 2. RunPod Secret for Tailscale

The Tailscale auth key is stored securely in RunPod Secrets:

1. Generate a Tailscale auth key at [admin/settings/keys](https://login.tailscale.com/admin/settings/keys):
   - ✅ Reusable
   - ✅ Ephemeral (auto-removes when pod terminates)
   - Tags: `tag:runpod`

2. Create a RunPod secret at [runpod.io/console/user/secrets](https://www.runpod.io/console/user/secrets):
   - **Name**: `ts_auth_key`
   - **Value**: Your Tailscale auth key (`tskey-auth-...`)

### 3. Tailscale ACLs

Add to your Tailscale ACL policy at [admin/acls](https://login.tailscale.com/admin/acls):

```json
{
  "tagOwners": {
    "tag:runpod": ["autogroup:admin"],
    "tag:server": ["autogroup:admin"]
  },
  "acls": [
    {
      "action": "accept",
      "src": ["tag:runpod"],
      "dst": ["tag:server:8000"]
    }
  ],
  "ssh": [
    {
      "action": "accept",
      "src": ["autogroup:admin"],
      "dst": ["tag:runpod"],
      "users": ["root"]
    }
  ]
}
```

### 4. Tag the Server

Ensure your cast2md server has the `tag:server` tag:

```bash
ssh root@cast2md "tailscale up --advertise-tags=tag:server"
```

### 5. Install Dependencies

```bash
pip install runpod httpx
```

## Usage

### Validate Configuration

```bash
source deploy/afterburner/.env
python deploy/afterburner/afterburner.py --dry-run
```

This checks:
- RunPod API connection
- Tailscale status
- Server connectivity
- Template creation/update

### Test Mode

Create pod, verify Tailscale connectivity, then terminate immediately:

```bash
python deploy/afterburner/afterburner.py --test
```

### Full Run

Process the entire transcription backlog:

```bash
python deploy/afterburner/afterburner.py
```

The script will:
1. Create/update the RunPod template
2. Create a GPU pod from the template
3. Wait for the startup script to install Tailscale and cast2md
4. Wait for pod to appear on your Tailscale network
5. Monitor queue progress
6. Auto-terminate when queue is empty
7. Report runtime and estimated cost

### Update Template Only

To update the template without creating a pod:

```bash
python deploy/afterburner/afterburner.py --update-template
```

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `RUNPOD_API_KEY` | Yes | - | RunPod API key |
| `CAST2MD_SERVER_URL` | Yes | - | Your cast2md server URL (Tailscale hostname) |
| `TS_HOSTNAME` | No | `runpod-afterburner` | Hostname on Tailscale |
| `RUNPOD_GPU_TYPE` | No | `NVIDIA GeForce RTX 4090` | GPU type to use |
| `GITHUB_REPO` | No | `meltforce/cast2md` | GitHub repo to install from |

### Terminate Existing Pods

If a previous run left a pod running:

```bash
python deploy/afterburner/afterburner.py --terminate-existing
```

## Cost Estimate

| GPU | Hourly Rate | 25hr Runtime | 50hr Runtime |
|-----|-------------|--------------|--------------|
| RTX 4090 | $0.34/hr | ~$8.50 | ~$17.00 |
| RTX 3090 | $0.22/hr | ~$5.50 | ~$11.00 |
| A40 | $0.39/hr | ~$9.75 | ~$19.50 |

Transcription speed is approximately 20x realtime with large-v3 model on RTX 4090.

## Troubleshooting

### Pod doesn't appear on Tailscale

1. Check the RunPod secret `ts_auth_key` is set correctly
2. Verify the auth key is valid and not expired
3. Check that the auth key has the `tag:runpod` tag
4. Check RunPod pod logs in the dashboard

### View startup logs

SSH into the pod via Tailscale (once connected):
```bash
ssh root@runpod-afterburner "cat /var/log/afterburner-startup.log"
```

### Node fails to start

Check the node logs on the pod:
```bash
ssh root@runpod-afterburner "tail -100 /tmp/cast2md-node.log"
```

### Server unreachable from pod

1. Verify ACLs allow `tag:runpod` → `tag:server:8000`
2. Check the server is tagged correctly
3. Test connectivity:
   ```bash
   ssh root@runpod-afterburner "curl -s $CAST2MD_SERVER_URL/api/health"
   ```

## Files

- `afterburner.py` - Main orchestration script
- `startup.sh` - Startup script that runs when pod boots (downloaded from GitHub)
- `.env.example` - Example environment configuration

## How the Template Works

1. **Template creation**: The script creates a RunPod template with:
   - Base image: `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`
   - Startup command: Downloads and runs `startup.sh` from GitHub
   - Environment variables including the Tailscale secret reference

2. **Pod boot sequence**:
   - Pod starts with the template
   - Startup script runs automatically
   - Installs ffmpeg, Tailscale, cast2md
   - Connects to Tailscale network
   - Starts the transcription worker

3. **Monitoring**: The local script waits for Tailscale to connect, then monitors the queue

## Security

- **Tailscale auth key**: Stored in RunPod Secrets, never exposed in code or logs
- **Ephemeral nodes**: Pods auto-remove from Tailscale when terminated
- **Network isolation**: Server only accessible via Tailscale (not public internet)
