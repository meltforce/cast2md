# RunPod Afterburner

On-demand GPU transcription worker that spins up a RunPod pod, processes the transcription backlog, and auto-terminates when complete.

## Architecture

```
Local Machine                    RunPod Pod                     cast2md Server
┌─────────────┐                 ┌─────────────────────┐        ┌─────────────┐
│             │  1. Create pod  │  tailscaled         │        │             │
│ afterburner ├────────────────►│  (userspace mode)   │        │   :8000     │
│    .py      │  (from template)│  HTTP proxy :1055   │        │             │
│             │                 │                     │        │  (Tailscale │
│             │  2. SSH setup   │  3. HTTP traffic    │        │    only)    │
│             ├────────────────►│     via proxy       ├───────►│             │
│             │  (ffmpeg, pip)  │                     │        │             │
│             │                 │  cast2md node       │        │             │
│             │  5. Terminate   │  (polls server)     │        │             │
│             │◄────────────────┤                     │        │             │
└─────────────┘   when done     └─────────────────────┘        └─────────────┘
```

The script uses a **two-phase approach**:

**Phase 1: Pod Bootstrap (automatic via template)**
1. Creates RunPod template with startup script
2. Creates pod from template
3. Startup script installs Tailscale in userspace mode with HTTP proxy
4. Pod appears on your Tailscale network

**Phase 2: SSH Setup (from local machine)**
5. Local script detects pod via Tailscale, verifies SSH connectivity
6. Installs ffmpeg, cast2md via SSH (for better visibility)
7. Registers node with server and starts worker
8. Monitors queue and terminates when empty

## Prerequisites

### 1. RunPod Account

1. Create account at [runpod.io](https://runpod.io)
2. Add credits (~$15 for 40+ hours of RTX 4090 time)
3. Generate API key: Settings → API Keys

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

### 5. Get Server Tailscale IP

Find your server's Tailscale IP (needed because MagicDNS doesn't work in containers):

```bash
tailscale status | grep cast2md
# Example output: 100.105.149.43   cast2md   tagged-devices   linux   -
```

### 6. Install Dependencies

```bash
pip install runpod httpx
```

### 7. Configure Environment

```bash
cp deploy/afterburner/.env.example deploy/afterburner/.env
# Edit .env with your values
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

Create pod, verify Tailscale connectivity, then terminate:

```bash
python deploy/afterburner/afterburner.py --test
```

Add `--keep-alive` to leave the pod running for debugging:

```bash
python deploy/afterburner/afterburner.py --test --keep-alive
```

### Full Run

Process the entire transcription backlog:

```bash
python deploy/afterburner/afterburner.py
```

The script will:
1. Create/update the RunPod template
2. Create a GPU pod from the template
3. Wait for Tailscale to connect (SSH verification)
4. Install ffmpeg and cast2md via SSH
5. Register node and start worker
6. Monitor queue progress
7. Auto-terminate when queue is empty
8. Report runtime and estimated cost

### Recreate Template

If you modify the startup script, force template recreation:

```bash
python deploy/afterburner/afterburner.py --recreate-template
```

### Terminate Existing Pods

If a previous run left a pod running:

```bash
python deploy/afterburner/afterburner.py --terminate-existing
```

### Use Existing Pod

For debugging, reuse an existing pod instead of creating a new one:

```bash
python deploy/afterburner/afterburner.py --use-existing --test
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `RUNPOD_API_KEY` | Yes | - | RunPod API key |
| `CAST2MD_SERVER_URL` | Yes | - | Your cast2md server URL (e.g., `https://cast2md.your-tailnet.ts.net`) |
| `CAST2MD_SERVER_IP` | Yes | - | Tailscale IP of the server (MagicDNS not available in containers) |
| `TS_HOSTNAME` | No | `runpod-afterburner` | Hostname on Tailscale |
| `RUNPOD_GPU_TYPE` | No | `NVIDIA GeForce RTX 4090` | GPU type to use |
| `GITHUB_REPO` | No | `meltforce/cast2md` | GitHub repo to install from |
| `NTFY_SERVER` | No | - | ntfy server URL (e.g., `https://ntfy.sh`) |
| `NTFY_TOPIC` | No | - | ntfy topic for notifications |
| `AFTERBURNER_MAX_RUNTIME` | No | - | Max runtime in seconds (safety limit) |

## Notifications

Afterburner can send notifications via [ntfy](https://ntfy.sh) at key events:

- **Pod Created**: When the GPU pod starts
- **Worker Ready**: When setup is complete and processing begins
- **Complete**: When queue is empty and pod terminates (includes runtime and cost)
- **Error**: On failures (high priority)
- **Max Runtime**: When the runtime limit is exceeded

To enable notifications:

```bash
export NTFY_SERVER=https://ntfy.sh
export NTFY_TOPIC=your-afterburner-topic
```

Then subscribe on your phone or desktop: `ntfy subscribe your-afterburner-topic`

## Safety Limits

To prevent runaway costs, you can set a maximum runtime:

```bash
export AFTERBURNER_MAX_RUNTIME=3600  # 1 hour max
```

The pod will be terminated when the limit is reached, even if jobs are still queued.

## Cost Estimate

| GPU | Hourly Rate | 25hr Runtime | 50hr Runtime |
|-----|-------------|--------------|--------------|
| RTX 4090 | $0.34/hr | ~$8.50 | ~$17.00 |
| RTX 3090 | $0.22/hr | ~$5.50 | ~$11.00 |
| A40 | $0.39/hr | ~$9.75 | ~$19.50 |

Transcription speed is approximately 20x realtime with large-v3 model on RTX 4090.

## Technical Details

### Tailscale Userspace Networking

RunPod containers don't have access to `/dev/net/tun`, so Tailscale must run in **userspace networking mode**:

```bash
tailscaled --tun=userspace-networking --state=/var/lib/tailscale/tailscaled.state --outbound-http-proxy-listen=localhost:1055 &
```

**Key implications:**

1. **No TUN interface**: Traffic doesn't go through a virtual network interface
2. **Inbound works**: SSH via Tailscale works normally (tailscaled handles it)
3. **Outbound requires proxy**: Applications must use the HTTP proxy for Tailscale traffic

### HTTP Proxy for Outbound Traffic

The `--outbound-http-proxy-listen=localhost:1055` flag creates an HTTP proxy that routes traffic through Tailscale.

**Important limitation**: This proxy only supports plain HTTP, not HTTPS CONNECT tunneling. For internal Tailscale traffic, we use HTTP on port 8000:

```bash
# Works (HTTP)
curl -x http://localhost:1055 http://100.105.149.43:8000/api/health

# Doesn't work (HTTPS - no CONNECT support)
curl -x http://localhost:1055 https://cast2md.leo-royal.ts.net/api/health
```

The setup script automatically converts `https://server` to `http://server:8000` for internal traffic.

### MagicDNS Not Available

MagicDNS (resolving `*.ts.net` hostnames) doesn't work in userspace mode. The script adds the server to `/etc/hosts`:

```bash
echo '100.105.149.43 cast2md.leo-royal.ts.net' >> /etc/hosts
```

This is why `CAST2MD_SERVER_IP` is required.

### Pod Detection Logic

When detecting the pod on Tailscale, the script:

1. Looks for hostnames starting with `runpod-afterburner*` (Tailscale adds `-1`, `-2` suffixes if name is taken)
2. Filters for `Online=true` status
3. Sorts by creation timestamp (newest first)
4. Verifies SSH connectivity before proceeding

This handles multiple orphaned nodes from previous test runs.

## Troubleshooting

### Pod doesn't appear on Tailscale

1. Check the RunPod secret `ts_auth_key` is set correctly
2. Verify the auth key is valid and not expired
3. Check that the auth key has the `tag:runpod` tag
4. Check RunPod pod logs in the dashboard

### SSH times out

1. Verify the pod shows `Online=true` in `tailscale status --json`
2. Multiple orphaned hosts? The script should pick the newest online one
3. Check ACLs allow SSH from your machine to `tag:runpod`

### Server unreachable from pod

1. Verify ACLs allow `tag:runpod` → `tag:server:8000`
2. Check the server has `tag:server`: `tailscale whois <server-ip>`
3. Verify HTTP proxy is running: `ss -tlnp | grep 1055`
4. Test with proxy:
   ```bash
   ssh root@<pod-ip> "curl -x http://localhost:1055 http://<server-ip>:8000/api/health"
   ```

### Node fails to register

1. Check the server IP in `/etc/hosts` is correct
2. Verify using HTTP (not HTTPS) to port 8000
3. Check proxy environment variable:
   ```bash
   ssh root@<pod-ip> "http_proxy=http://localhost:1055 cast2md node register --server 'http://<server>:8000' --name 'Test'"
   ```

### View logs

```bash
# Startup script output (in container's stdout)
# View in RunPod dashboard

# Node worker logs
ssh root@<pod-ip> "tail -100 /tmp/cast2md-node.log"

# Tailscale status
ssh root@<pod-ip> "tailscale status"
```

## Files

- `afterburner.py` - Main orchestration script (startup script embedded inline)
- `startup.sh` - Reference copy of startup script (actual script is in afterburner.py)
- `.env.example` - Example environment configuration

## Security

- **Tailscale auth key**: Stored in RunPod Secrets, never exposed in code or logs
- **Ephemeral nodes**: Pods auto-remove from Tailscale when terminated
- **Network isolation**: Server only accessible via Tailscale (not public internet)
- **HTTP internal traffic**: Uses HTTP:8000 internally, but traffic is encrypted by Tailscale's WireGuard tunnel
