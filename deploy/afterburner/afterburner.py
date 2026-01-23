#!/usr/bin/env python3
"""
RunPod Afterburner - On-demand GPU transcription worker.

Spins up a GPU pod on RunPod using a custom template that automatically:
- Connects to Tailscale network
- Installs cast2md from GitHub
- Starts the transcription worker

The pod auto-terminates when the queue is empty.

Prerequisites:
1. Create RunPod secret named "ts_auth_key" with your Tailscale auth key
2. Set RUNPOD_API_KEY environment variable

Usage:
    python deploy/afterburner/afterburner.py
    python deploy/afterburner/afterburner.py --dry-run
    python deploy/afterburner/afterburner.py --test
"""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx


TEMPLATE_NAME = "cast2md-afterburner"

# Inline startup script (embedded because repo is private)
STARTUP_SCRIPT = '''
#!/bin/bash
set -e

echo "=== Afterburner Startup $(date) ==="

# Required env vars from template
: "${TS_AUTH_KEY:?TS_AUTH_KEY is required}"
: "${TS_HOSTNAME:=runpod-afterburner}"
: "${CAST2MD_SERVER_URL:?CAST2MD_SERVER_URL is required}"
: "${GITHUB_REPO:=meltforce/cast2md}"

echo "Config: TS_HOSTNAME=$TS_HOSTNAME SERVER=$CAST2MD_SERVER_URL"

# Install dependencies
apt-get update -qq && apt-get install -y -qq ffmpeg curl > /dev/null

# Install Tailscale
curl -fsSL https://tailscale.com/install.sh | sh

# Start tailscaled in userspace mode
tailscaled --tun=userspace-networking --state=/var/lib/tailscale/tailscaled.state &
sleep 3

# Connect to Tailscale
tailscale up --auth-key="$TS_AUTH_KEY" --hostname="$TS_HOSTNAME" --ssh --accept-routes --accept-dns
echo "Tailscale connected!"

# Install cast2md
pip install "cast2md[node] @ git+https://github.com/${GITHUB_REPO}.git"
cast2md --version

# Register and start node
cast2md node register --server "$CAST2MD_SERVER_URL" --name "RunPod Afterburner"
cast2md node start
'''


@dataclass
class Config:
    """Afterburner configuration."""

    runpod_api_key: str
    server_url: str  # Required - your cast2md server URL
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

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        runpod_api_key = os.environ.get("RUNPOD_API_KEY")
        if not runpod_api_key:
            raise ValueError("RUNPOD_API_KEY environment variable required")

        server_url = os.environ.get("CAST2MD_SERVER_URL")
        if not server_url:
            raise ValueError("CAST2MD_SERVER_URL environment variable required")

        return cls(
            runpod_api_key=runpod_api_key,
            server_url=server_url,
            ts_hostname=os.environ.get("TS_HOSTNAME", "runpod-afterburner"),
            gpu_type=os.environ.get("RUNPOD_GPU_TYPE", cls.gpu_type),
            github_repo=os.environ.get("GITHUB_REPO", "meltforce/cast2md"),
        )


def log(msg: str, level: str = "INFO") -> None:
    """Log a message with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}")


class RunPodAPI:
    """RunPod REST API client."""

    BASE_URL = "https://rest.runpod.io/v1"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )

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


def get_startup_command(config: Config) -> list[str]:
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
        "GITHUB_REPO": config.github_repo,
    }


def ensure_template(config: Config) -> str:
    """Ensure the afterburner template exists and is up to date. Returns template ID."""
    api = RunPodAPI(config.runpod_api_key)

    # Check if template already exists
    templates = api.get_templates()
    existing = next((t for t in templates if t.get("name") == TEMPLATE_NAME), None)

    # Base template data (shared between create and update)
    base_data = {
        "name": TEMPLATE_NAME,
        "imageName": config.image_name,
        "dockerStartCmd": get_startup_command(config),
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
        # Only update if needed - for now just use the existing template
        # The update API has stricter field requirements
        return template_id
    else:
        log("Creating new template...")
        # Create requires isServerless
        create_data = {**base_data, "isServerless": False}
        result = api.create_template(create_data)
        template_id = result["id"]
        log(f"Created template: {template_id}")
        return template_id


def create_pod(config: Config, template_id: str) -> tuple[dict, str]:
    """Create a RunPod pod using the template, trying fallback GPU types if needed.

    Returns:
        Tuple of (pod dict, gpu_type used)
    """
    import runpod

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
            pod = runpod.create_pod(
                name="cast2md-afterburner",
                template_id=template_id,
                gpu_type_id=gpu_type,
                cloud_type=config.cloud_type,
                start_ssh=True,
                support_public_ip=True,
            )
            log(f"Created pod: {pod['id']} with {gpu_type}")
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
    import runpod

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


def wait_for_tailscale(config: Config, timeout: int = 600) -> bool:
    """Wait for the pod to appear on Tailscale."""
    log(f"Waiting for {config.ts_hostname} to appear on Tailscale...")

    start_time = time.time()
    while time.time() - start_time < timeout:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            status = json.loads(result.stdout)
            peers = status.get("Peer", {})
            for peer_id, peer in peers.items():
                if peer.get("HostName") == config.ts_hostname:
                    if peer.get("Online", False):
                        log(f"Found {config.ts_hostname} on Tailscale!")
                        return True

        time.sleep(10)

    log(f"Timeout waiting for {config.ts_hostname}", "ERROR")
    return False


def get_queue_status(config: Config) -> dict:
    """Get the current queue status from the server."""
    response = httpx.get(f"{config.server_url}/api/queue/status", timeout=10.0)
    response.raise_for_status()
    return response.json()


def monitor_queue(config: Config) -> None:
    """Monitor the queue and wait until it's empty."""
    log("Monitoring queue status...")

    consecutive_empty = 0
    required_empty_checks = 2  # Require queue to be empty twice in a row

    while True:
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

        except Exception as e:
            log(f"Error checking queue: {e}", "WARN")
            time.sleep(config.poll_interval)


def terminate_pod(config: Config, pod_id: str) -> None:
    """Terminate the RunPod pod."""
    import runpod

    runpod.api_key = config.runpod_api_key

    log(f"Terminating pod {pod_id}...")
    runpod.terminate_pod(pod_id)
    log("Pod terminated")


def get_running_pods(config: Config) -> list[dict]:
    """Get list of running cast2md-afterburner pods."""
    import runpod

    runpod.api_key = config.runpod_api_key

    pods = runpod.get_pods()
    return [p for p in pods if p.get("name") == "cast2md-afterburner"]


def estimate_cost(start_time: datetime, gpu_type: str) -> tuple[float, str]:
    """Estimate the cost based on runtime."""
    # Approximate hourly rates for common GPUs (RunPod community cloud, Jan 2025)
    rates = {
        "NVIDIA GeForce RTX 4090": 0.34,
        "NVIDIA GeForce RTX 4080": 0.28,
        "NVIDIA GeForce RTX 3090": 0.22,
        "NVIDIA RTX A4000": 0.16,
        "NVIDIA RTX A5000": 0.22,
        "NVIDIA A40": 0.39,
        "NVIDIA A100 80GB PCIe": 1.19,
    }

    runtime = datetime.now() - start_time
    hours = runtime.total_seconds() / 3600
    rate = rates.get(gpu_type, 0.40)  # Default rate if unknown
    cost = hours * rate

    runtime_str = str(runtime).split(".")[0]  # Remove microseconds
    return cost, runtime_str


def tailscale_ssh_cmd(hostname: str, cmd: str, check: bool = True, retries: int = 1) -> str:
    """Run a command on the pod via Tailscale SSH."""
    last_error = None
    for attempt in range(retries):
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", f"root@{hostname}", cmd],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        last_error = result.stderr
        if attempt < retries - 1:
            log(f"SSH command failed, retrying ({attempt + 1}/{retries})...", "DEBUG")
            time.sleep(5)

    if check:
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    return result.stdout.strip()


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
        "--update-template",
        action="store_true",
        help="Update the template without creating a pod",
    )
    args = parser.parse_args()

    try:
        config = Config.from_env()
    except ValueError as e:
        log(str(e), "ERROR")
        sys.exit(1)

    log("RunPod Afterburner")
    log(f"Server: {config.server_url}")
    log(f"GPU: {config.gpu_type}")
    log(f"Tailscale hostname: {config.ts_hostname}")

    if args.dry_run:
        log("Dry run - validating configuration...")

        # Check RunPod connection
        try:
            import runpod

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
            template_id = ensure_template(config)
            log(f"Template: Ready ({template_id})")
        except Exception as e:
            log(f"Template: Failed - {e}", "ERROR")
            sys.exit(1)

        log("Configuration valid!")
        return

    if args.update_template:
        log("Updating template...")
        template_id = ensure_template(config)
        log(f"Template updated: {template_id}")
        return

    # Check for existing pods
    existing_pods = get_running_pods(config)
    if existing_pods:
        if args.terminate_existing:
            for pod in existing_pods:
                log(f"Terminating existing pod {pod['id']}...")
                terminate_pod(config, pod["id"])
            time.sleep(5)
        else:
            log(f"Found {len(existing_pods)} existing afterburner pod(s)", "WARN")
            log("Use --terminate-existing to remove them first")
            sys.exit(1)

    # Ensure template exists
    template_id = ensure_template(config)

    start_time = datetime.now()
    pod = None
    gpu_used = config.gpu_type

    try:
        # Create pod from template (with fallback to other GPU types)
        pod, gpu_used = create_pod(config, template_id)
        pod_id = pod["id"]

        # Wait for pod to be running
        wait_for_pod_running(config, pod_id)

        # Wait for Tailscale connection (startup script handles setup)
        log("Waiting for startup script to complete and Tailscale to connect...")
        if not wait_for_tailscale(config):
            raise RuntimeError("Pod failed to connect to Tailscale")

        if args.test:
            # Wait for startup script to complete (installs dependencies, tailscale, cast2md)
            log("Test mode - waiting for startup script to complete...")
            time.sleep(30)  # Give time for pip install

            log("Verifying connectivity...")
            result = tailscale_ssh_cmd(config.ts_hostname, "cast2md --version", retries=6)
            log(f"cast2md version: {result}")
            log("Test successful!")
            return

        # For full run, wait for worker to start
        time.sleep(30)

        # Monitor queue until empty
        monitor_queue(config)

    except KeyboardInterrupt:
        log("Interrupted by user", "WARN")
    except Exception as e:
        log(f"Error: {e}", "ERROR")
        raise
    finally:
        if pod:
            cost, runtime = estimate_cost(start_time, gpu_used)
            log(f"Runtime: {runtime}")
            log(f"Estimated cost: ${cost:.2f} ({gpu_used})")
            terminate_pod(config, pod["id"])

    log("Afterburner complete!")


if __name__ == "__main__":
    main()
