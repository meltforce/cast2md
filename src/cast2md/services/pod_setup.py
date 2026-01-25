"""Shared pod setup logic for RunPod workers.

This module centralizes the SSH commands needed to set up a pod for transcription.
Used by both the server-side RunPod service and the CLI afterburner script.
"""

import logging
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class PodSetupConfig:
    """Configuration for pod setup."""

    # Server connection
    server_url: str  # e.g., https://cast2md.ts.net
    server_ip: str  # Tailscale IP for /etc/hosts
    node_name: str  # Name to register with server

    # Transcription
    model: str  # e.g., "parakeet-tdt-0.6b-v3" or "large-v3-turbo"

    # Installation
    github_repo: str = "meltforce/cast2md"  # For pip install

    # Worker behavior
    idle_timeout_minutes: int = 10  # Auto-terminate after this many minutes idle (0 to disable)
    persistent: bool = False  # Dev mode: disable auto-termination

    @property
    def is_parakeet(self) -> bool:
        """Check if this is a Parakeet model."""
        return "parakeet" in self.model.lower()

    @property
    def transcription_backend(self) -> str:
        """Get the transcription backend for this model."""
        return "parakeet" if self.is_parakeet else "whisper"

    @property
    def internal_url(self) -> str:
        """Convert server URL to HTTP:8000 for internal Tailscale traffic."""
        url = self.server_url
        if url.startswith("https://"):
            url = "http://" + url[8:]
        elif not url.startswith("http://"):
            url = "http://" + url
        if ":8000" not in url:
            url = url.rstrip("/") + ":8000"
        return url

    @property
    def server_host(self) -> str:
        """Extract hostname from server URL."""
        return self.server_url.replace("https://", "").replace("http://", "").split("/")[0]


def setup_pod(
    config: PodSetupConfig,
    run_ssh: Callable[[str, str, int], None],
) -> None:
    """Set up a pod for transcription.

    Args:
        config: Pod setup configuration.
        run_ssh: Function to run SSH commands. Signature: (command, description, timeout=120).
    """
    # Add server to /etc/hosts (MagicDNS not available in userspace mode)
    run_ssh(
        f"grep -q '{config.server_host}' /etc/hosts || echo '{config.server_ip} {config.server_host}' >> /etc/hosts",
        "Adding server to /etc/hosts",
        120,
    )

    # Install ffmpeg (skip if already installed - pre-configured image)
    run_ssh(
        "which ffmpeg > /dev/null || (apt-get update -qq && apt-get install -y -qq ffmpeg > /dev/null 2>&1)",
        "Installing ffmpeg",
        120,
    )

    # Install NeMo toolkit for Parakeet models (skip if already installed - pre-configured image)
    if config.is_parakeet:
        run_ssh(
            "python -c 'import nemo' 2>/dev/null || pip install --no-cache-dir 'nemo_toolkit[asr]'",
            "Installing NeMo toolkit for Parakeet",
            900,
        )

    # Install cast2md (always install to get latest code)
    run_ssh(
        f"pip install --no-cache-dir 'cast2md[node] @ git+https://github.com/{config.github_repo}.git'",
        "Installing cast2md",
        600,
    )

    # Register node
    run_ssh(
        f"http_proxy=http://localhost:1055 cast2md node register --server '{config.internal_url}' --name '{config.node_name}'",
        "Registering node",
        120,
    )

    # Start worker with appropriate backend and termination settings
    backend_env = f"TRANSCRIPTION_BACKEND={config.transcription_backend}"
    idle_env = f"NODE_IDLE_TIMEOUT_MINUTES={config.idle_timeout_minutes}"
    persistent_env = f"NODE_PERSISTENT={'1' if config.persistent else '0'}"
    run_ssh(
        f"http_proxy=http://localhost:1055 {backend_env} {idle_env} {persistent_env} WHISPER_MODEL={config.model} "
        "nohup cast2md node start > /tmp/cast2md-node.log 2>&1 &",
        "Starting worker",
        120,
    )

    # Start watchdog that terminates the pod when worker exits (unless persistent)
    if not config.persistent:
        run_ssh(
            "nohup bash -c '"
            "while pgrep -f \"cast2md node\" > /dev/null; do sleep 5; done; "
            "echo \"Worker exited, terminating pod...\" >> /tmp/cast2md-node.log; "
            "runpodctl stop"
            "' > /tmp/watchdog.log 2>&1 &",
            "Starting watchdog",
            120,
        )
