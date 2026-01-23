# RunPod Afterburner

On-demand GPU transcription worker that spins up a RunPod pod, processes the transcription backlog, and auto-terminates when complete.

## Architecture

```
Local Machine                    RunPod Pod                     cast2md Server
┌─────────────┐                 ┌─────────────┐                ┌─────────────┐
│             │  1. Create pod  │             │                │             │
│ afterburner ├────────────────►│  GPU Worker │                │   Server    │
│    .py      │                 │             │                │             │
│             │  2. SSH setup   │  Tailscale  │  4. Connect    │  (Tailscale │
│             ├────────────────►│  installed  ├───────────────►│    only)    │
│             │  (RunPod SSH)   │             │                │             │
│             │                 │             │                │             │
│             │  3. Install     │  5. Process │  Poll jobs     │             │
│             ├────────────────►│     jobs    │◄──────────────►│             │
│             │  (Tailscale)    │             │                │             │
│             │                 │             │                │             │
│             │  6. Terminate   │             │                │             │
│             │◄────────────────┤             │                │             │
└─────────────┘   when done     └─────────────┘                └─────────────┘
```

The script uses a two-stage SSH approach:
1. **RunPod SSH** (public IP:port) - Used to install Tailscale and system dependencies
2. **Tailscale SSH** - Used for cast2md installation and secure server communication

## Prerequisites

### 1. RunPod Account

1. Create account at [runpod.io](https://runpod.io)
2. Add credits (~$15 for 40+ hours of RTX 4090 time)
3. Generate API key: Settings → API Keys
4. Store as environment variable:
   ```bash
   export RUNPOD_API_KEY="your-api-key"
   ```

### 2. Tailscale Auth Key

1. Go to [Tailscale Admin Console](https://login.tailscale.com/admin/settings/keys)
2. Generate new auth key with these settings:
   - ✅ Reusable
   - ✅ Ephemeral (auto-removes when pod terminates)
   - Tags: `tag:runpod`
3. Store as environment variable:
   ```bash
   export TS_AUTH_KEY="tskey-auth-..."
   ```

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
pip install runpod httpx build
```

## Usage

### Validate Configuration

```bash
python deploy/afterburner/afterburner.py --dry-run
```

This checks:
- RunPod API connection
- Tailscale status
- Server connectivity
- Wheel build (if using wheel mode)

### Test Mode

Create pod, verify connectivity, then terminate immediately:

```bash
python deploy/afterburner/afterburner.py --test
```

### Full Run

Process the entire transcription backlog:

```bash
python deploy/afterburner/afterburner.py
```

The script will:
1. Build a wheel package (if wheel mode)
2. Create a RunPod GPU pod
3. Wait for pod to be running
4. SSH via RunPod to install Tailscale and ffmpeg
5. Wait for pod to appear on your Tailscale network
6. Upload and install cast2md via Tailscale SSH
7. Register the node with the server
8. Start the transcriber worker
9. Monitor queue progress
10. Auto-terminate when queue is empty
11. Report runtime and estimated cost

### Installation Modes

**Wheel Mode (default)** - For private repos:
```bash
python deploy/afterburner/afterburner.py --mode=wheel
```
- Builds wheel locally
- Uploads via Tailscale SSH
- Works with private repositories

**GitHub Mode** - For public repos:
```bash
python deploy/afterburner/afterburner.py --mode=github
```
- Installs directly from GitHub
- No local build required
- Enables future server-side auto-scaling

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `RUNPOD_API_KEY` | Yes | - | RunPod API key |
| `TS_AUTH_KEY` | Yes | - | Tailscale auth key (ephemeral, reusable) |
| `CAST2MD_SERVER_URL` | No | `https://cast2md.leo-royal.ts.net` | Server URL |
| `TS_HOSTNAME` | No | `runpod-afterburner` | Hostname on Tailscale |
| `RUNPOD_GPU_TYPE` | No | `NVIDIA GeForce RTX 4090` | GPU type to use |
| `AFTERBURNER_MODE` | No | `wheel` | Installation mode |

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

1. Check the auth key is valid and not expired
2. Verify the auth key has the `tag:runpod` tag
3. Check RunPod pod logs in the dashboard

### SSH connection refused

1. Wait 30-60 seconds after Tailscale connects
2. Check that SSH is enabled in Tailscale ACLs
3. Verify your user has access to the `tag:runpod` tag

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
   ssh root@runpod-afterburner "curl -s https://cast2md.leo-royal.ts.net/api/health"
   ```

## Files

- `afterburner.py` - Main orchestration script
- `node-setup.sh` - Reference script documenting the setup steps (commands are run via SSH by afterburner.py)

## Future: Server-Side Auto-Scaling

Once the repository is public, the server could trigger pods automatically:

```python
# In server scheduler
if pending_jobs > THRESHOLD and no_active_afterburner():
    spawn_afterburner_pod()  # Uses GitHub mode
```

This would eliminate the need for manual triggering.
