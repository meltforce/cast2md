#!/usr/bin/env python3
"""
RunPod Afterburner - On-demand GPU transcription worker.

Spins up a GPU pod on RunPod using a custom template that automatically:
- Connects to Tailscale network
- Installs cast2md from GitHub
- Starts the transcription worker

The pod auto-terminates when the queue is empty.

Supports parallel execution - multiple instances can run simultaneously,
each with a unique instance ID (e.g., "a3f2") for Tailscale hostname and node name.

Prerequisites:
1. Create RunPod secret named "ts_auth_key" with your Tailscale auth key
2. Set RUNPOD_API_KEY environment variable

Usage:
    python deploy/afterburner/afterburner.py
    python deploy/afterburner/afterburner.py --dry-run
    python deploy/afterburner/afterburner.py --test
    python deploy/afterburner/afterburner.py --terminate-all
"""

import argparse
import ipaddress
import json
import os
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime

import httpx
import runpod


TEMPLATE_NAME = "cast2md-afterburner"

# Inline startup script - minimal, just sets up Tailscale
# All other setup (ffmpeg, cast2md, etc.) is done via SSH for visibility
STARTUP_SCRIPT = '''
#!/bin/bash
set -e

echo "=== Afterburner Startup $(date) ==="

# Required env vars from template
: "${TS_AUTH_KEY:?TS_AUTH_KEY is required}"
: "${TS_HOSTNAME:=runpod-afterburner}"

echo "Config: TS_HOSTNAME=$TS_HOSTNAME"

# === TAILSCALE SETUP ===
echo "Installing Tailscale..."
curl -fsSL https://tailscale.com/install.sh | sh

echo "Starting tailscaled (userspace networking with HTTP proxy)..."
tailscaled --tun=userspace-networking --state=/var/lib/tailscale/tailscaled.state --outbound-http-proxy-listen=localhost:1055 &

# Wait for tailscaled to be ready
echo "Waiting for tailscaled socket..."
SOCKET_READY=false
for i in {1..30}; do
    if [ -S /var/run/tailscale/tailscaled.sock ]; then
        echo "tailscaled ready"
        SOCKET_READY=true
        break
    fi
    sleep 1
done

if [ "$SOCKET_READY" != "true" ]; then
    echo "ERROR: tailscaled socket not ready after 30s"
    exit 1
fi

echo "Connecting to Tailscale as $TS_HOSTNAME..."
tailscale up --auth-key="$TS_AUTH_KEY" --hostname="$TS_HOSTNAME" --ssh --accept-dns

echo "Tailscale connected!"
tailscale status

# Export proxy for applications
export http_proxy=http://localhost:1055
export https_proxy=http://localhost:1055
echo "HTTP proxy available at localhost:1055"

# Keep container alive - setup continues via SSH from afterburner.py
echo "Container ready - waiting for SSH setup..."
tail -f /dev/null
'''


@dataclass
class Config:
    """Afterburner configuration."""

    runpod_api_key: str
    server_url: str  # Required - your cast2md server URL
    server_ip: str  # Required - Tailscale IP of server (MagicDNS not available in pod)
    ts_hostname: str = "runpod-afterburner"
    gpu_type: str = "NVIDIA GeForce RTX 4090"
    image_name: str = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
    cloud_type: str = "ALL"  # Let RunPod find available GPUs
    poll_interval: int = 30
    empty_queue_wait: int = 60
    github_repo: str = "meltforce/cast2md"
    # Fallback GPU types in order of preference
    gpu_fallbacks: tuple = (
        "NVIDIA GeForce RTX 4090",
        "NVIDIA GeForce RTX 3090",
        "NVIDIA RTX A4000",
        "NVIDIA RTX A5000",
        "NVIDIA GeForce RTX 4080",
    )
    # Optional ntfy notifications
    ntfy_server: str | None = None
    ntfy_topic: str | None = None
    # Safety limits
    max_runtime: int | None = None  # Max runtime in seconds (None = unlimited)
    # Whisper model for transcription
    whisper_model: str = "large-v3-turbo"

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        runpod_api_key = os.environ.get("RUNPOD_API_KEY")
        if not runpod_api_key:
            raise ValueError("RUNPOD_API_KEY environment variable required")

        server_url = os.environ.get("CAST2MD_SERVER_URL")
        if not server_url:
            raise ValueError("CAST2MD_SERVER_URL environment variable required")

        server_ip = os.environ.get("CAST2MD_SERVER_IP")
        if not server_ip:
            raise ValueError("CAST2MD_SERVER_IP environment variable required (Tailscale IP of server)")

        # Validate IP address format
        try:
            ipaddress.ip_address(server_ip)
        except ValueError:
            raise ValueError(f"CAST2MD_SERVER_IP is not a valid IP address: {server_ip}")

        # Parse max runtime
        max_runtime_str = os.environ.get("AFTERBURNER_MAX_RUNTIME")
        max_runtime = int(max_runtime_str) if max_runtime_str else None

        return cls(
            runpod_api_key=runpod_api_key,
            server_url=server_url,
            server_ip=server_ip,
            ts_hostname=os.environ.get("TS_HOSTNAME", "runpod-afterburner"),
            gpu_type=os.environ.get("RUNPOD_GPU_TYPE", cls.gpu_type),
            github_repo=os.environ.get("GITHUB_REPO", "meltforce/cast2md"),
            ntfy_server=os.environ.get("NTFY_SERVER"),
            ntfy_topic=os.environ.get("NTFY_TOPIC"),
            max_runtime=max_runtime,
            whisper_model=os.environ.get("WHISPER_MODEL", "large-v3-turbo"),
        )


def log(msg: str, level: str = "INFO") -> None:
    """Log a message with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}")


def notify(
    config: Config,
    title: str,
    message: str,
    priority: str = "default",
    tags: list[str] | None = None,
) -> None:
    """Send a notification via ntfy if configured."""
    if not config.ntfy_server or not config.ntfy_topic:
        return

    try:
        headers = {"Title": title, "Priority": priority}
        if tags:
            headers["Tags"] = ",".join(tags)

        response = httpx.post(
            f"{config.ntfy_server}/{config.ntfy_topic}",
            content=message,
            headers=headers,
            timeout=10.0,
        )
        response.raise_for_status()
    except Exception as e:
        log(f"Failed to send notification: {e}", "WARN")


class RunPodAPI:
    """RunPod REST API client."""

    BASE_URL = "https://rest.runpod.io/v1"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        """Close the HTTP client."""
        self.client.close()

    def get_templates(self) -> list[dict]:
        """Get all templates."""
        response = self.client.get(f"{self.BASE_URL}/templates")
        response.raise_for_status()
        return response.json()

    def create_template(self, template_data: dict) -> dict:
        """Create a new template."""
        response = self.client.post(f"{self.BASE_URL}/templates", json=template_data)
        if response.status_code >= 400:
            log(f"Template creation failed: {response.text}", "DEBUG")
        response.raise_for_status()
        return response.json()

    def update_template(self, template_id: str, template_data: dict) -> dict:
        """Update an existing template."""
        response = self.client.patch(
            f"{self.BASE_URL}/templates/{template_id}", json=template_data
        )
        if response.status_code >= 400:
            log(f"Template update failed: {response.text}", "DEBUG")
        response.raise_for_status()
        return response.json()

    def delete_template(self, template_id: str) -> None:
        """Delete a template."""
        response = self.client.delete(f"{self.BASE_URL}/templates/{template_id}")
        response.raise_for_status()


def get_startup_command() -> list[str]:
    """Generate the startup command that runs when the pod boots."""
    # Run the inline startup script
    return ["bash", "-c", STARTUP_SCRIPT]


def get_template_env(config: Config) -> dict:
    """Get environment variables for the template."""
    return {
        # Tailscale auth key from RunPod secret (created by user)
        "TS_AUTH_KEY": "{{ RUNPOD_SECRET_ts_auth_key }}",
        # Dynamic config
        "TS_HOSTNAME": config.ts_hostname,
        "CAST2MD_SERVER_URL": config.server_url,
        "CAST2MD_SERVER_IP": config.server_ip,  # Tailscale IP (MagicDNS not available in pod)
        "GITHUB_REPO": config.github_repo,
    }


def ensure_template(config: Config, recreate: bool = False) -> str:
    """Ensure the afterburner template exists and is up to date. Returns template ID."""
    with RunPodAPI(config.runpod_api_key) as api:
        # Check if template already exists
        templates = api.get_templates()
        existing = next((t for t in templates if t.get("name") == TEMPLATE_NAME), None)

        # Delete existing template if recreating
        if existing and recreate:
            log(f"Deleting existing template: {existing['id']}")
            api.delete_template(existing["id"])
            existing = None

        # Base template data (shared between create and update)
        base_data = {
            "name": TEMPLATE_NAME,
            "imageName": config.image_name,
            "dockerStartCmd": get_startup_command(),
            "containerDiskInGb": 20,
            "volumeInGb": 0,
            "ports": ["22/tcp"],
            "isPublic": False,
            "env": get_template_env(config),
            "readme": "cast2md Afterburner - On-demand GPU transcription worker",
        }

        if existing:
            template_id = existing["id"]
            log(f"Using existing template: {template_id}")
            return template_id
        else:
            log("Creating new template...")
            # Create requires isServerless
            create_data = {**base_data, "isServerless": False}
            result = api.create_template(create_data)
            template_id = result["id"]
            log(f"Created template: {template_id}")
            return template_id


def create_pod(
    config: Config,
    template_id: str,
    pod_name: str = "cast2md-afterburner",
    env_overrides: dict | None = None,
) -> tuple[dict, str]:
    """Create a RunPod pod using the template, trying fallback GPU types if needed.

    Args:
        config: Afterburner configuration
        template_id: RunPod template ID
        pod_name: Name for the pod (supports unique suffixes for parallel execution)
        env_overrides: Environment variable overrides (e.g., {"TS_HOSTNAME": "unique-name"})

    Returns:
        Tuple of (pod dict, gpu_type used)
    """
    runpod.api_key = config.runpod_api_key

    # Build list of GPUs to try - start with configured, then fallbacks
    gpu_types = [config.gpu_type]
    for fallback in config.gpu_fallbacks:
        if fallback not in gpu_types:
            gpu_types.append(fallback)

    last_error = None
    for gpu_type in gpu_types:
        log(f"Trying to create pod with {gpu_type}...")
        try:
            create_kwargs = {
                "name": pod_name,
                "template_id": template_id,
                "gpu_type_id": gpu_type,
                "cloud_type": config.cloud_type,
                "start_ssh": True,
                "support_public_ip": True,
            }
            if env_overrides:
                create_kwargs["env"] = env_overrides

            pod = runpod.create_pod(**create_kwargs)
            log(f"Created pod: {pod['id']} ({pod_name}) with {gpu_type}")
            return pod, gpu_type
        except Exception as e:
            error_msg = str(e)
            if "resources" in error_msg.lower() or "not have" in error_msg.lower():
                log(f"{gpu_type} not available, trying next...", "WARN")
                last_error = e
                continue
            else:
                # Different error, re-raise
                raise

    # All GPU types failed
    raise RuntimeError(f"No GPU available. Last error: {last_error}")


def wait_for_pod_running(config: Config, pod_id: str, timeout: int = 300) -> None:
    """Wait for pod to be running."""
    runpod.api_key = config.runpod_api_key

    log("Waiting for pod to be running...")
    start_time = time.time()

    while time.time() - start_time < timeout:
        pod = runpod.get_pod(pod_id)

        if pod is None:
            log("Pod not found, waiting...", "WARN")
            time.sleep(5)
            continue

        status = pod.get("desiredStatus", "")
        runtime = pod.get("runtime") or {}

        log(f"Pod status: {status}", "DEBUG")

        if status == "RUNNING" and runtime:
            log("Pod is running!")
            return

        # Check for errors
        if status in ("EXITED", "ERROR"):
            raise RuntimeError(f"Pod failed to start: {status}")

        time.sleep(5)

    raise RuntimeError("Timeout waiting for pod to be running")


def wait_for_tailscale(config: Config, ts_hostname: str | None = None, timeout: int = 600) -> str | None:
    """Wait for the pod to appear on Tailscale and be reachable via SSH.

    Args:
        config: Afterburner configuration
        ts_hostname: Override hostname to search for (supports instance-specific names)
        timeout: Maximum seconds to wait

    Returns the Tailscale IP of the pod, or None on timeout.
    """
    hostname_prefix = ts_hostname or config.ts_hostname
    log(f"Waiting for {hostname_prefix}* to appear on Tailscale...")

    start_time = time.time()

    while time.time() - start_time < timeout:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            status = json.loads(result.stdout)
            # Find all matching candidates
            candidates = []
            peers = status.get("Peer", {})
            for _, peer in peers.items():
                hostname = peer.get("HostName", "")
                online = peer.get("Online", False)

                # Match prefix - Tailscale adds -1, -2 etc. when name is taken
                if hostname.startswith(hostname_prefix) and online:
                    candidates.append(peer)

            # Sort candidates by creation time (newest first)
            def get_created_time(p):
                created_str = p.get("Created", "")
                if not created_str:
                    return datetime.min
                try:
                    # Handle Z suffix for UTC
                    if created_str.endswith("Z"):
                        created_str = created_str[:-1] + "+00:00"
                    return datetime.fromisoformat(created_str)
                except ValueError:
                    return datetime.min

            candidates.sort(key=get_created_time, reverse=True)

            # Try candidates
            for peer in candidates:
                hostname = peer.get("HostName")
                ip = peer.get("TailscaleIPs", ["?"])[0]
                created = peer.get("Created", "unknown")
                
                # Skip invalid IPs
                if ip == "?":
                    continue

                # Check SSH connectivity directly (more robust than ping)
                log(f"Checking candidate: {hostname} ({ip}) Created={created}", "DEBUG")
                
                ssh_cmd = [
                    "ssh",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=5",
                    "-o", "BatchMode=yes",
                    f"root@{ip}",
                    "exit 0"
                ]
                
                result = subprocess.run(
                    ssh_cmd,
                    capture_output=True,
                    text=True,
                )
                
                if result.returncode == 0:
                    log(f"SSH handshake successful: {ip}")
                    return ip
                else:
                    log(f"SSH failed for {hostname} ({ip}): {result.stderr.strip()}", "DEBUG")
            
            # If we get here, no candidates worked
            if candidates:
                log(f"Tried {len(candidates)} candidates, none reachable yet", "DEBUG")

        time.sleep(5)

    log(f"Timeout waiting for {hostname_prefix}*", "ERROR")
    return None


def get_queue_status(config: Config, retries: int = 3) -> dict:
    """Get the current queue status from the server with retry logic."""
    last_error = None
    for attempt in range(retries):
        try:
            response = httpx.get(f"{config.server_url}/api/queue/status", timeout=10.0)
            response.raise_for_status()
            return response.json()
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            last_error = e
            if attempt < retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                log(f"Queue status request failed, retrying in {wait_time}s: {e}", "WARN")
                time.sleep(wait_time)
    raise last_error


class MaxRuntimeExceeded(Exception):
    """Raised when max runtime limit is exceeded."""

    pass


def monitor_queue(config: Config, start_time: datetime) -> None:
    """Monitor the queue and wait until it's empty."""
    log("Monitoring queue status...")
    if config.max_runtime:
        log(f"Max runtime limit: {config.max_runtime}s")

    consecutive_empty = 0
    required_empty_checks = 2  # Require queue to be empty twice in a row

    while True:
        # Check max runtime limit
        if config.max_runtime:
            elapsed = (datetime.now() - start_time).total_seconds()
            if elapsed >= config.max_runtime:
                log(f"Max runtime exceeded ({elapsed:.0f}s >= {config.max_runtime}s)", "WARN")
                notify(
                    config,
                    "Afterburner: Max Runtime",
                    f"Max runtime limit ({config.max_runtime}s) exceeded. Terminating pod.",
                    priority="high",
                    tags=["warning", "clock"],
                )
                raise MaxRuntimeExceeded(f"Max runtime exceeded: {elapsed:.0f}s")

        try:
            status = get_queue_status(config)

            # Count total pending/running jobs across all queues
            total_pending = 0
            total_running = 0
            for queue_name in [
                "download_queue",
                "transcript_download_queue",
                "transcribe_queue",
            ]:
                queue = status.get(queue_name, {})
                total_pending += queue.get("queued", 0)
                total_running += queue.get("running", 0)

            transcribe = status.get("transcribe_queue", {})
            log(
                f"Queue: {total_pending} pending, {total_running} running "
                f"(transcribe: {transcribe.get('queued', 0)} queued, {transcribe.get('running', 0)} running)"
            )

            if total_pending == 0 and total_running == 0:
                consecutive_empty += 1
                if consecutive_empty >= required_empty_checks:
                    log("Queue empty - ready to terminate")
                    return
                log(
                    f"Queue appears empty, waiting to confirm ({consecutive_empty}/{required_empty_checks})..."
                )
                time.sleep(config.empty_queue_wait)
            else:
                consecutive_empty = 0
                time.sleep(config.poll_interval)

        except MaxRuntimeExceeded:
            raise
        except Exception as e:
            log(f"Error checking queue: {e}", "WARN")
            time.sleep(config.poll_interval)


def terminate_pod(config: Config, pod_id: str) -> None:
    """Terminate the RunPod pod."""
    runpod.api_key = config.runpod_api_key

    log(f"Terminating pod {pod_id}...")
    runpod.terminate_pod(pod_id)
    log("Pod terminated")


def get_running_pods(config: Config) -> list[dict]:
    """Get list of running cast2md-afterburner pods (prefix match for parallel support)."""
    runpod.api_key = config.runpod_api_key

    pods = runpod.get_pods()
    return [p for p in pods if p.get("name", "").startswith("cast2md-afterburner")]


def get_gpu_hourly_rate(gpu_type: str) -> float | None:
    """Fetch live GPU pricing from RunPod API.

    Returns the community cloud hourly rate, or None if unavailable.
    """
    try:
        gpu_info = runpod.get_gpu(gpu_type)
        if gpu_info and "communityPrice" in gpu_info:
            return float(gpu_info["communityPrice"])
    except Exception as e:
        log(f"Failed to fetch live GPU pricing: {e}", "DEBUG")
    return None


def estimate_cost(start_time: datetime, gpu_type: str) -> tuple[float, str]:
    """Estimate the cost based on runtime using live pricing when available."""
    # Try to get live pricing first
    rate = get_gpu_hourly_rate(gpu_type)

    # Fallback to cached rates if API fails
    if rate is None:
        fallback_rates = {
            "NVIDIA GeForce RTX 4090": 0.34,
            "NVIDIA GeForce RTX 4080": 0.28,
            "NVIDIA GeForce RTX 3090": 0.22,
            "NVIDIA RTX A4000": 0.16,
            "NVIDIA RTX A5000": 0.22,
            "NVIDIA A40": 0.39,
            "NVIDIA A100 80GB PCIe": 1.19,
        }
        rate = fallback_rates.get(gpu_type, 0.40)
        log(f"Using fallback rate for {gpu_type}: ${rate}/hr", "DEBUG")

    runtime = datetime.now() - start_time
    hours = runtime.total_seconds() / 3600
    cost = hours * rate

    runtime_str = str(runtime).split(".")[0]  # Remove microseconds
    return cost, runtime_str


def setup_pod_via_ssh(config: Config, host_ip: str, node_name: str = "RunPod Afterburner") -> None:
    """Install cast2md and dependencies on the pod via SSH.

    Args:
        config: Afterburner configuration
        host_ip: Tailscale IP of the pod
        node_name: Name to register the node with (supports unique suffixes)
    """

    def run_ssh(cmd: str, description: str, timeout: int = 300) -> str:
        """Run SSH command with logging and timeout."""
        log(f"{description}...")
        ssh_cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=30",
            f"root@{host_ip}",
            cmd
        ]
        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            log(f"Command timed out after {timeout}s", "ERROR")
            raise RuntimeError(f"SSH command timed out: {description}")
        if result.returncode != 0:
            log(f"Command failed: {result.stderr}", "ERROR")
            raise RuntimeError(f"SSH command failed: {description}")
        if result.stdout.strip():
            # Show last few lines of output
            lines = result.stdout.strip().split('\n')
            for line in lines[-5:]:
                log(f"  {line}", "DEBUG")
        return result.stdout.strip()

    # Add server to /etc/hosts (MagicDNS workaround)
    server_host = config.server_url.replace("https://", "").replace("http://", "").split("/")[0]
    run_ssh(
        f"echo '{config.server_ip} {server_host}' >> /etc/hosts",
        f"Adding {server_host} -> {config.server_ip} to /etc/hosts"
    )

    # Install system dependencies
    run_ssh(
        "apt-get update -qq && apt-get install -y -qq ffmpeg > /dev/null 2>&1",
        "Installing ffmpeg"
    )

    # Install cast2md from GitHub (longer timeout for pip install)
    run_ssh(
        f"pip install --no-cache-dir 'cast2md[node] @ git+https://github.com/{config.github_repo}.git'",
        f"Installing cast2md from {config.github_repo}",
        timeout=600  # 10 minutes for pip install
    )

    # Verify installation
    version = run_ssh("cast2md --version", "Verifying cast2md installation")
    log(f"Installed: {version}")

    # Convert URL to HTTP:8000 for internal Tailscale traffic
    # (The HTTP proxy only supports plain HTTP, not HTTPS CONNECT)
    internal_server_url = config.server_url
    if internal_server_url.startswith("https://"):
        internal_server_url = "http://" + internal_server_url[8:]
    elif not internal_server_url.startswith("http://"):
        internal_server_url = "http://" + internal_server_url
    if ":8000" not in internal_server_url:
        internal_server_url = internal_server_url.rstrip("/") + ":8000"

    # Register node with server (use HTTP proxy for Tailscale traffic)
    run_ssh(
        f"http_proxy=http://localhost:1055 "
        f"cast2md node register --server '{internal_server_url}' --name '{node_name}'",
        "Registering node with server"
    )

    # Start node worker in background (use HTTP proxy for Tailscale traffic)
    run_ssh(
        f"http_proxy=http://localhost:1055 "
        f"WHISPER_MODEL={config.whisper_model} "
        "nohup cast2md node start > /tmp/cast2md-node.log 2>&1 &",
        "Starting node worker"
    )

    # Verify worker started successfully
    time.sleep(3)  # Give it a moment to start
    run_ssh(
        "pgrep -f 'cast2md node' > /dev/null || (echo 'Worker not running!' && cat /tmp/cast2md-node.log && exit 1)",
        "Verifying worker is running"
    )

    log("Pod setup complete!")


def main():
    parser = argparse.ArgumentParser(description="RunPod Afterburner")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration without creating pod",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Create pod, verify Tailscale connectivity, then terminate",
    )
    parser.add_argument(
        "--terminate-existing",
        action="store_true",
        help="Terminate any existing afterburner pods first",
    )
    parser.add_argument(
        "--terminate-all",
        action="store_true",
        help="Terminate all running afterburner pods and exit",
    )
    parser.add_argument(
        "--update-template",
        action="store_true",
        help="Update the template without creating a pod",
    )
    parser.add_argument(
        "--recreate-template",
        action="store_true",
        help="Delete and recreate the template (use when startup script changes)",
    )
    parser.add_argument(
        "--keep-alive",
        action="store_true",
        help="Don't terminate pod on exit (for debugging)",
    )
    parser.add_argument(
        "--use-existing",
        action="store_true",
        help="Use existing pod instead of creating new one (for debugging)",
    )
    args = parser.parse_args()

    try:
        config = Config.from_env()
    except ValueError as e:
        log(str(e), "ERROR")
        sys.exit(1)

    # Generate unique instance ID for parallel execution
    instance_id = secrets.token_hex(2)  # e.g., "a3f2"
    ts_hostname = f"{config.ts_hostname}-{instance_id}"
    pod_name = f"cast2md-afterburner-{instance_id}"
    node_name = f"RunPod Afterburner {instance_id}"

    log("RunPod Afterburner")
    log(f"Instance: {instance_id}")
    log(f"Server: {config.server_url}")
    log(f"GPU: {config.gpu_type}")
    log(f"Tailscale hostname: {ts_hostname}")

    # Handle --terminate-all early
    if args.terminate_all:
        existing_pods = get_running_pods(config)
        if not existing_pods:
            log("No afterburner pods to terminate")
            return
        log(f"Terminating {len(existing_pods)} pod(s)...")
        for p in existing_pods:
            log(f"  Terminating {p['id']} ({p.get('name', 'unknown')})")
            terminate_pod(config, p["id"])
        log("All pods terminated")
        return

    if args.dry_run:
        log("Dry run - validating configuration...")

        # Check RunPod connection
        try:
            runpod.api_key = config.runpod_api_key
            pods = runpod.get_pods()
            log(f"RunPod: Connected ({len(pods)} existing pods)")
        except Exception as e:
            log(f"RunPod: Connection failed - {e}", "ERROR")
            sys.exit(1)

        # Check Tailscale
        result = subprocess.run(
            ["tailscale", "status"],
            capture_output=True,
        )
        if result.returncode == 0:
            log("Tailscale: Connected")
        else:
            log("Tailscale: Not connected", "ERROR")
            sys.exit(1)

        # Check server connectivity
        try:
            status = get_queue_status(config)
            pending = status.get("transcribe_queue", {}).get("queued", 0)
            log(f"Server: Connected ({pending} transcribe jobs pending)")
        except Exception as e:
            log(f"Server: Connection failed - {e}", "ERROR")
            sys.exit(1)

        # Check/create template
        try:
            template_id = ensure_template(config, recreate=args.recreate_template)
            log(f"Template: Ready ({template_id})")
        except Exception as e:
            log(f"Template: Failed - {e}", "ERROR")
            sys.exit(1)

        log("Configuration valid!")
        return

    if args.update_template:
        log("Updating template...")
        template_id = ensure_template(config, recreate=args.recreate_template)
        log(f"Template updated: {template_id}")
        return

    # Check for existing pods
    existing_pods = get_running_pods(config)

    # Handle --use-existing mode
    if args.use_existing:
        if not existing_pods:
            log("No existing afterburner pods found", "ERROR")
            sys.exit(1)
        pod = existing_pods[0]
        log(f"Using existing pod: {pod['id']}")

        # Just test Tailscale connectivity
        log("Waiting for Tailscale to connect...")
        host_ip = wait_for_tailscale(config)
        if not host_ip:
            log("Pod not found on Tailscale", "ERROR")
            sys.exit(1)

        log(f"Pod reachable at: {host_ip}")
        log(f"Try: ssh root@{host_ip}")

        if args.test:
            # Try SSH
            log("Testing SSH...")
            ssh_result = subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
                 f"root@{host_ip}", "echo 'SSH works!' && hostname"],
                capture_output=True, text=True, timeout=30
            )
            if ssh_result.returncode == 0:
                log(f"SSH output: {ssh_result.stdout.strip()}")
                log("Test successful!")
            else:
                log(f"SSH failed: {ssh_result.stderr.strip()}", "ERROR")
                sys.exit(1)

        log("Pod will keep running. Press Enter to exit...")
        try:
            input()
        except EOFError:
            pass  # Running non-interactively
        return

    if existing_pods:
        if args.terminate_existing:
            for p in existing_pods:
                log(f"Terminating existing pod {p['id']}...")
                terminate_pod(config, p["id"])
            time.sleep(5)
        else:
            # Parallel execution supported - just log a warning
            log(f"Found {len(existing_pods)} existing afterburner pod(s) - running in parallel", "WARN")

    # Ensure template exists
    template_id = ensure_template(config, recreate=args.recreate_template)

    start_time = datetime.now()
    pod = None
    gpu_used = config.gpu_type

    exit_reason = "completed"
    try:
        # Create pod from template (with fallback to other GPU types)
        # Pass unique TS_HOSTNAME for parallel execution
        env_overrides = {"TS_HOSTNAME": ts_hostname}
        pod, gpu_used = create_pod(config, template_id, pod_name, env_overrides)
        pod_id = pod["id"]

        notify(
            config,
            "Afterburner: Pod Created",
            f"Pod {pod_id} ({instance_id}) created with {gpu_used}",
            tags=["rocket"],
        )

        # Wait for pod to be running
        wait_for_pod_running(config, pod_id)

        # Wait for Tailscale connection (startup script handles setup)
        log("Waiting for Tailscale to connect...")
        host_ip = wait_for_tailscale(config, ts_hostname)
        if not host_ip:
            raise RuntimeError("Pod failed to connect to Tailscale")

        if args.test:
            log(f"Pod reachable at: {host_ip}")
            log(f"Try: ssh root@{host_ip}")
            log("Press Enter to terminate the pod (or Ctrl+C to keep it running)...")
            try:
                input()
            except (KeyboardInterrupt, EOFError):
                log("Keeping pod alive", "WARN")
                args.keep_alive = True
            return

        # Install dependencies via SSH (safer than in startup script)
        setup_pod_via_ssh(config, host_ip, node_name)

        notify(
            config,
            "Afterburner: Worker Ready",
            f"Worker connected and processing queue ({gpu_used})",
            tags=["white_check_mark"],
        )

        # For full run, wait for worker to start
        time.sleep(10)

        # Monitor queue until empty
        monitor_queue(config, start_time)

    except KeyboardInterrupt:
        log("Interrupted by user", "WARN")
        exit_reason = "interrupted"
    except MaxRuntimeExceeded:
        exit_reason = "max_runtime"
    except Exception as e:
        log(f"Error: {e}", "ERROR")
        exit_reason = "error"
        notify(
            config,
            "Afterburner: Error",
            f"Error: {e}",
            priority="high",
            tags=["x"],
        )
        raise
    finally:
        if pod:
            cost, runtime = estimate_cost(start_time, gpu_used)
            log(f"Runtime: {runtime}")
            log(f"Estimated cost: ${cost:.2f} ({gpu_used})")
            if args.keep_alive:
                log(f"Pod {pod['id']} left running (--keep-alive)")
                log(f"Terminate manually: python afterburner.py --terminate-all")
            else:
                terminate_pod(config, pod["id"])
                if exit_reason == "completed":
                    notify(
                        config,
                        "Afterburner: Complete",
                        f"Queue empty. Runtime: {runtime}, Cost: ${cost:.2f}",
                        tags=["tada"],
                    )

    log("Afterburner complete!")


if __name__ == "__main__":
    main()
