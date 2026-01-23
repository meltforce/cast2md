#!/usr/bin/env python3
"""
RunPod Afterburner - On-demand GPU transcription worker.

Spins up a GPU pod on RunPod, connects it to the Tailscale network,
processes the transcription backlog, and auto-terminates when complete.

Usage:
    python deploy/afterburner/afterburner.py
    python deploy/afterburner/afterburner.py --dry-run
    python deploy/afterburner/afterburner.py --test
    python deploy/afterburner/afterburner.py --mode=github  # For public repo
"""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Config:
    """Afterburner configuration."""

    runpod_api_key: str
    ts_auth_key: str
    server_url: str
    ts_hostname: str = "runpod-afterburner"
    gpu_type: str = "NVIDIA GeForce RTX 4090"
    image_name: str = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
    cloud_type: str = "COMMUNITY"
    poll_interval: int = 30
    empty_queue_wait: int = 60
    mode: str = "wheel"  # "wheel" or "github"
    github_repo: str = "meltforce/cast2md"

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        runpod_api_key = os.environ.get("RUNPOD_API_KEY")
        if not runpod_api_key:
            raise ValueError("RUNPOD_API_KEY environment variable required")

        ts_auth_key = os.environ.get("TS_AUTH_KEY")
        if not ts_auth_key:
            raise ValueError("TS_AUTH_KEY environment variable required")

        server_url = os.environ.get(
            "CAST2MD_SERVER_URL", "https://cast2md.leo-royal.ts.net"
        )

        return cls(
            runpod_api_key=runpod_api_key,
            ts_auth_key=ts_auth_key,
            server_url=server_url,
            ts_hostname=os.environ.get("TS_HOSTNAME", "runpod-afterburner"),
            gpu_type=os.environ.get("RUNPOD_GPU_TYPE", cls.gpu_type),
            mode=os.environ.get("AFTERBURNER_MODE", "wheel"),
        )


@dataclass
class PodSSHInfo:
    """SSH connection info for a pod."""

    host: str
    port: int
    user: str = "root"


def log(msg: str, level: str = "INFO") -> None:
    """Log a message with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}")


def run_cmd(cmd: list[str], check: bool = True, capture: bool = False) -> str | None:
    """Run a shell command."""
    log(f"Running: {' '.join(cmd)}", "DEBUG")
    result = subprocess.run(
        cmd, capture_output=capture, text=True, check=check
    )
    if capture:
        return result.stdout.strip()
    return None


def build_wheel() -> Path:
    """Build a wheel package for cast2md."""
    log("Building wheel package...")

    # Find project root (where pyproject.toml is)
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent

    # Build wheel
    dist_dir = project_root / "dist"
    run_cmd([sys.executable, "-m", "build", str(project_root), "--wheel"])

    # Find the built wheel
    wheels = list(dist_dir.glob("cast2md-*.whl"))
    if not wheels:
        raise RuntimeError("No wheel found after build")

    wheel_path = max(wheels, key=lambda p: p.stat().st_mtime)
    log(f"Built wheel: {wheel_path}")
    return wheel_path


def create_pod(config: Config) -> dict:
    """Create a RunPod pod with GPU."""
    import runpod

    runpod.api_key = config.runpod_api_key

    log(f"Creating pod with {config.gpu_type}...")

    # Create pod - we'll run setup via SSH after it starts
    pod = runpod.create_pod(
        name="cast2md-afterburner",
        image_name=config.image_name,
        gpu_type_id=config.gpu_type,
        cloud_type=config.cloud_type,
        volume_in_gb=0,  # No persistent volume needed
        container_disk_in_gb=20,
        # Store config in env vars for later use
        env={
            "TS_AUTH_KEY": config.ts_auth_key,
            "TS_HOSTNAME": config.ts_hostname,
        },
    )

    log(f"Created pod: {pod['id']}")
    return pod


def wait_for_pod_running(config: Config, pod_id: str, timeout: int = 300) -> PodSSHInfo:
    """Wait for pod to be running and return SSH connection info."""
    import runpod

    runpod.api_key = config.runpod_api_key

    log("Waiting for pod to be running...")
    start_time = time.time()

    while time.time() - start_time < timeout:
        pod = runpod.get_pod(pod_id)

        status = pod.get("desiredStatus", "")
        runtime = pod.get("runtime", {})

        if status == "RUNNING" and runtime:
            # Extract SSH info from pod details
            ports = runtime.get("ports", [])
            for port_info in ports:
                if port_info.get("privatePort") == 22:
                    public_ip = port_info.get("ip")
                    public_port = port_info.get("publicPort")
                    if public_ip and public_port:
                        log(f"Pod running! SSH: {public_ip}:{public_port}")
                        return PodSSHInfo(host=public_ip, port=public_port)

        # Check for errors
        if status in ("EXITED", "ERROR"):
            raise RuntimeError(f"Pod failed to start: {status}")

        time.sleep(5)

    raise RuntimeError("Timeout waiting for pod to be running")


def wait_for_tailscale(config: Config, timeout: int = 300) -> bool:
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

        time.sleep(5)

    log(f"Timeout waiting for {config.ts_hostname}", "ERROR")
    return False


def runpod_ssh_cmd(ssh_info: PodSSHInfo, cmd: str, check: bool = True) -> str:
    """Run a command on the pod via RunPod's public SSH."""
    ssh_opts = [
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=30",
        "-p", str(ssh_info.port),
    ]
    result = subprocess.run(
        ["ssh", *ssh_opts, f"{ssh_info.user}@{ssh_info.host}", cmd],
        capture_output=True,
        text=True,
        check=check,
    )
    return result.stdout.strip()


def tailscale_ssh_cmd(hostname: str, cmd: str, check: bool = True) -> str:
    """Run a command on the pod via Tailscale SSH."""
    result = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no", f"root@{hostname}", cmd],
        capture_output=True,
        text=True,
        check=check,
    )
    return result.stdout.strip()


def setup_tailscale_on_pod(config: Config, ssh_info: PodSSHInfo) -> None:
    """Install and configure Tailscale on the pod via RunPod SSH."""
    log("Installing Tailscale on pod...")

    # Install dependencies and Tailscale
    commands = [
        # Install system dependencies
        "apt-get update -qq && apt-get install -y -qq ffmpeg curl > /dev/null",
        # Install Tailscale
        "curl -fsSL https://tailscale.com/install.sh | sh",
        # Start tailscaled in userspace mode (background)
        "nohup tailscaled --tun=userspace-networking --state=/var/lib/tailscale/tailscaled.state > /var/log/tailscaled.log 2>&1 &",
    ]

    for cmd in commands:
        log(f"Running: {cmd[:60]}...")
        runpod_ssh_cmd(ssh_info, cmd)

    # Wait for tailscaled to be ready
    time.sleep(3)

    # Connect to Tailscale using env vars
    ts_auth_key = config.ts_auth_key
    ts_hostname = config.ts_hostname

    log("Connecting to Tailscale network...")
    connect_cmd = (
        f'tailscale up '
        f'--auth-key="{ts_auth_key}" '
        f'--hostname="{ts_hostname}" '
        f'--ssh '
        f'--accept-routes'
    )
    runpod_ssh_cmd(ssh_info, connect_cmd)

    log("Tailscale setup complete!")


def setup_node_wheel(config: Config, wheel_path: Path) -> None:
    """Set up the node using wheel mode (SCP + install)."""
    hostname = config.ts_hostname

    log("Uploading wheel to pod via Tailscale...")
    subprocess.run(
        [
            "scp",
            "-o", "StrictHostKeyChecking=no",
            str(wheel_path),
            f"root@{hostname}:/tmp/",
        ],
        check=True,
    )

    wheel_name = wheel_path.name
    log("Installing cast2md on pod...")
    tailscale_ssh_cmd(hostname, f"pip install '/tmp/{wheel_name}[node]'")


def setup_node_github(config: Config) -> None:
    """Set up the node using GitHub mode (direct install from repo)."""
    hostname = config.ts_hostname

    log(f"Installing cast2md from GitHub ({config.github_repo})...")
    tailscale_ssh_cmd(
        hostname,
        f'pip install "cast2md[node] @ git+https://github.com/{config.github_repo}.git"',
    )


def register_and_start_node(config: Config) -> None:
    """Register the node with the server and start the worker."""
    hostname = config.ts_hostname

    log("Registering node with server...")
    tailscale_ssh_cmd(
        hostname,
        f'cast2md node register --server {config.server_url} --name "RunPod Afterburner"',
    )

    log("Starting transcriber node...")
    # Run in background with nohup, redirect output to a log file
    tailscale_ssh_cmd(
        hostname,
        "nohup cast2md node start > /tmp/cast2md-node.log 2>&1 &",
        check=False,
    )

    # Wait a moment for the node to start
    time.sleep(5)

    # Check if it's running
    result = tailscale_ssh_cmd(hostname, "pgrep -f 'cast2md node start'", check=False)
    if result:
        log("Node started successfully")
    else:
        log("Failed to start node - checking logs...", "ERROR")
        logs = tailscale_ssh_cmd(hostname, "tail -50 /tmp/cast2md-node.log", check=False)
        print(logs)
        raise RuntimeError("Node failed to start")


def get_queue_status(config: Config) -> dict:
    """Get the current queue status from the server."""
    import httpx

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
            for queue_name in ["download_queue", "transcript_download_queue", "transcribe_queue"]:
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
                log(f"Queue appears empty, waiting to confirm ({consecutive_empty}/{required_empty_checks})...")
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
    # Approximate hourly rates for common GPUs
    rates = {
        "NVIDIA GeForce RTX 4090": 0.34,
        "NVIDIA GeForce RTX 3090": 0.22,
        "NVIDIA A40": 0.39,
        "NVIDIA A100 80GB PCIe": 1.19,
    }

    runtime = datetime.now() - start_time
    hours = runtime.total_seconds() / 3600
    rate = rates.get(gpu_type, 0.40)  # Default rate if unknown
    cost = hours * rate

    runtime_str = str(runtime).split(".")[0]  # Remove microseconds
    return cost, runtime_str


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
        help="Create pod, verify connectivity, then terminate",
    )
    parser.add_argument(
        "--mode",
        choices=["wheel", "github"],
        default=None,
        help="Installation mode: wheel (build and upload) or github (install from repo)",
    )
    parser.add_argument(
        "--terminate-existing",
        action="store_true",
        help="Terminate any existing afterburner pods first",
    )
    args = parser.parse_args()

    try:
        config = Config.from_env()
        if args.mode:
            config.mode = args.mode
    except ValueError as e:
        log(str(e), "ERROR")
        sys.exit(1)

    log("RunPod Afterburner")
    log(f"Server: {config.server_url}")
    log(f"GPU: {config.gpu_type}")
    log(f"Mode: {config.mode}")
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

        if config.mode == "wheel":
            # Check if we can build
            try:
                wheel_path = build_wheel()
                log(f"Wheel: Built successfully ({wheel_path.name})")
            except Exception as e:
                log(f"Wheel: Build failed - {e}", "ERROR")
                sys.exit(1)

        log("Configuration valid!")
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

    # Build wheel if needed
    wheel_path = None
    if config.mode == "wheel":
        wheel_path = build_wheel()

    start_time = datetime.now()
    pod = None

    try:
        # Create pod
        pod = create_pod(config)
        pod_id = pod["id"]

        # Wait for pod to be running and get SSH info
        ssh_info = wait_for_pod_running(config, pod_id)

        # Wait a bit for SSH to be fully ready
        log("Waiting for SSH to be ready...")
        time.sleep(10)

        # Set up Tailscale on the pod via RunPod SSH
        setup_tailscale_on_pod(config, ssh_info)

        # Wait for Tailscale connection from our side
        if not wait_for_tailscale(config):
            raise RuntimeError("Pod failed to connect to Tailscale")

        # Give Tailscale SSH a moment to be ready
        time.sleep(5)

        # Install cast2md via Tailscale SSH
        if config.mode == "wheel":
            setup_node_wheel(config, wheel_path)
        else:
            setup_node_github(config)

        if args.test:
            log("Test mode - verifying connectivity...")
            result = tailscale_ssh_cmd(config.ts_hostname, "cast2md --version")
            log(f"cast2md version: {result}")
            log("Test successful!")
            return

        # Register and start node
        register_and_start_node(config)

        # Monitor queue until empty
        monitor_queue(config)

    except KeyboardInterrupt:
        log("Interrupted by user", "WARN")
    except Exception as e:
        log(f"Error: {e}", "ERROR")
        raise
    finally:
        if pod:
            cost, runtime = estimate_cost(start_time, config.gpu_type)
            log(f"Runtime: {runtime}")
            log(f"Estimated cost: ${cost:.2f}")
            terminate_pod(config, pod["id"])

    log("Afterburner complete!")


if __name__ == "__main__":
    main()
