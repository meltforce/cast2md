"""RunPod GPU worker pod management service."""

import ipaddress
import json
import logging
import secrets
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from urllib.parse import urlparse

import httpx

from cast2md.config.settings import Settings, get_settings, reload_settings

logger = logging.getLogger(__name__)

# Try to import runpod - it's optional
try:
    import runpod

    RUNPOD_AVAILABLE = True
except ImportError:
    runpod = None  # type: ignore
    RUNPOD_AVAILABLE = False


class PodSetupPhase(str, Enum):
    """Phases of pod setup."""

    CREATING = "creating"  # Creating pod on RunPod
    STARTING = "starting"  # Waiting for pod to reach RUNNING status
    CONNECTING = "connecting"  # Waiting for Tailscale connection
    INSTALLING = "installing"  # SSH setup: ffmpeg, cast2md, register
    READY = "ready"  # Worker is running
    FAILED = "failed"  # Setup failed


@dataclass
class PodSetupState:
    """Tracks the setup state of a pod."""

    instance_id: str
    pod_id: str | None = None
    pod_name: str = ""
    ts_hostname: str = ""
    node_name: str = ""
    gpu_type: str = ""
    phase: PodSetupPhase = PodSetupPhase.CREATING
    message: str = ""
    started_at: datetime = field(default_factory=datetime.now)
    error: str | None = None
    host_ip: str | None = None


@dataclass
class PodInfo:
    """Information about a running pod."""

    id: str
    name: str
    status: str
    gpu_type: str
    created_at: str | None = None


class RunPodService:
    """Manages RunPod GPU worker pods."""

    # Template name used for all afterburner pods
    TEMPLATE_NAME = "cast2md-afterburner"

    # Startup script for pods (minimal - just Tailscale setup)
    STARTUP_SCRIPT = '''#!/bin/bash
set -e

echo "=== Afterburner Startup $(date) ==="

: "${TS_AUTH_KEY:?TS_AUTH_KEY is required}"
: "${TS_HOSTNAME:=runpod-afterburner}"

echo "Config: TS_HOSTNAME=$TS_HOSTNAME"

echo "Installing Tailscale..."
curl -fsSL https://tailscale.com/install.sh | sh

echo "Starting tailscaled (userspace networking with HTTP proxy)..."
tailscaled --tun=userspace-networking --state=/var/lib/tailscale/tailscaled.state --outbound-http-proxy-listen=localhost:1055 &

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

export http_proxy=http://localhost:1055
export https_proxy=http://localhost:1055
echo "HTTP proxy available at localhost:1055"

echo "Container ready - waiting for SSH setup..."
tail -f /dev/null
'''

    def __init__(self, settings: Settings | None = None):
        self._initial_settings = settings
        self._setup_states: dict[str, PodSetupState] = {}
        self._lock = threading.Lock()
        self._template_id: str | None = None

    @property
    def settings(self) -> Settings:
        """Get current settings (reloaded for runtime changes)."""
        if self._initial_settings:
            return self._initial_settings
        reload_settings()
        return get_settings()

    def is_available(self) -> bool:
        """Check if RunPod feature is available (library + API key present).

        Note: Tailscale auth key is stored in RunPod Secrets, not on server.
        """
        if not RUNPOD_AVAILABLE:
            return False
        return bool(self.settings.runpod_api_key)

    def get_server_tailscale_info(self) -> tuple[str | None, str | None]:
        """Get this server's Tailscale hostname and IP.

        Returns (hostname, ip) or (None, None) if not available.
        """
        try:
            result = subprocess.run(
                ["tailscale", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return None, None

            status = json.loads(result.stdout)
            self_status = status.get("Self", {})

            # Get hostname (e.g., "cast2md" from "cast2md.leo-royal.ts.net")
            dns_name = self_status.get("DNSName", "")
            hostname = dns_name.rstrip(".") if dns_name else None

            # Get Tailscale IP
            ips = self_status.get("TailscaleIPs", [])
            ip = ips[0] if ips else None

            return hostname, ip
        except Exception as e:
            logger.warning(f"Failed to get Tailscale info: {e}")
            return None, None

    def get_effective_server_url(self) -> str | None:
        """Get the server URL for pods to connect to.

        Uses configured value if set, otherwise auto-derives from Tailscale.
        """
        if self.settings.runpod_server_url:
            return self.settings.runpod_server_url

        hostname, _ = self.get_server_tailscale_info()
        if hostname:
            return f"https://{hostname}"
        return None

    def get_effective_server_ip(self) -> str | None:
        """Get the server IP for pods to use.

        Uses configured value if set, otherwise auto-derives from Tailscale.
        """
        if self.settings.runpod_server_ip:
            return self.settings.runpod_server_ip

        _, ip = self.get_server_tailscale_info()
        return ip

    def is_enabled(self) -> bool:
        """Check if RunPod is enabled and configured."""
        return self.is_available() and self.settings.runpod_enabled

    def can_create_pod(self) -> tuple[bool, str]:
        """Check if we can create another pod.

        Returns:
            Tuple of (can_create, reason)
        """
        if not self.is_available():
            return False, "RunPod not configured (missing API key)"
        if not self.settings.runpod_enabled:
            return False, "RunPod not enabled"

        server_url = self.get_effective_server_url()
        server_ip = self.get_effective_server_ip()

        if not server_url or not server_ip:
            return False, "Server not on Tailscale (cannot derive URL/IP)"

        # Validate server IP
        try:
            ipaddress.ip_address(server_ip)
        except ValueError:
            return False, f"Invalid server IP: {server_ip}"

        current = len(self.list_pods())
        creating = len([s for s in self._setup_states.values() if s.phase not in (PodSetupPhase.READY, PodSetupPhase.FAILED)])
        total = current + creating

        if total >= self.settings.runpod_max_pods:
            return False, f"Max pods ({self.settings.runpod_max_pods}) reached ({current} running, {creating} creating)"

        return True, ""

    def list_pods(self) -> list[PodInfo]:
        """List all active afterburner pods."""
        if not self.is_available():
            return []

        runpod.api_key = self.settings.runpod_api_key
        try:
            pods = runpod.get_pods()
            return [
                PodInfo(
                    id=p["id"],
                    name=p.get("name", "unknown"),
                    status=p.get("desiredStatus", "unknown"),
                    gpu_type=p.get("machine", {}).get("gpuDisplayName", "unknown"),
                    created_at=p.get("createdAt"),
                )
                for p in pods
                if p.get("name", "").startswith("cast2md-afterburner")
            ]
        except Exception as e:
            logger.error(f"Failed to list RunPod pods: {e}")
            return []

    # GPUs suitable for Whisper transcription (good price/performance, sufficient VRAM)
    # Excludes datacenter-class GPUs (A100, H100, MI300X) that are overkill
    ALLOWED_GPU_PREFIXES = [
        "NVIDIA GeForce RTX 4090",
        "NVIDIA GeForce RTX 4080",
        "NVIDIA GeForce RTX 3090",
        "NVIDIA GeForce RTX 3080",
        "NVIDIA RTX A4000",
        "NVIDIA RTX A4500",
        "NVIDIA RTX A5000",
        "NVIDIA RTX A6000",
        "NVIDIA L4",
        "NVIDIA L40",
        "NVIDIA RTX 4000",
        "NVIDIA RTX 5000",
        "NVIDIA RTX 6000",
    ]

    # Maximum hourly price to show (filters out expensive options)
    MAX_GPU_PRICE = 1.00  # $/hr

    # Minimum VRAM for Whisper large-v3
    MIN_GPU_VRAM = 16  # GB

    # Cache settings
    GPU_CACHE_KEY = "_runpod_gpu_cache"
    GPU_CACHE_MAX_AGE_DAYS = 7

    def get_available_gpus(self, force_refresh: bool = False) -> list[dict]:
        """Get available GPU types with pricing (cached).

        Returns list of dicts with id, display_name, memory_gb, price_hr.
        Uses cached data if available and fresh, otherwise fetches from API.
        """
        if not self.is_available():
            return []

        # Try to get from cache
        if not force_refresh:
            cached = self._get_gpu_cache()
            if cached is not None:
                return cached

        # Fetch fresh data and cache it
        return self.refresh_gpu_cache()

    def refresh_gpu_cache(self) -> list[dict]:
        """Fetch GPU data from API and update cache. Returns the GPU list."""
        if not self.is_available():
            return []

        gpus = self._fetch_gpus_from_api()
        self._set_gpu_cache(gpus)
        return gpus

    def _get_gpu_cache(self) -> list[dict] | None:
        """Get cached GPU data if fresh, else None."""
        from cast2md.db.connection import get_db
        from cast2md.db.repository import SettingsRepository

        try:
            with get_db() as conn:
                repo = SettingsRepository(conn)
                cached_json = repo.get(self.GPU_CACHE_KEY)

            if not cached_json:
                return None

            cached = json.loads(cached_json)
            cached_at = datetime.fromisoformat(cached.get("cached_at", ""))
            age_days = (datetime.now() - cached_at).days

            if age_days >= self.GPU_CACHE_MAX_AGE_DAYS:
                logger.info(f"GPU cache expired ({age_days} days old)")
                return None

            return cached.get("gpus", [])
        except Exception as e:
            logger.warning(f"Failed to read GPU cache: {e}")
            return None

    def _set_gpu_cache(self, gpus: list[dict]) -> None:
        """Store GPU data in cache."""
        from cast2md.db.connection import get_db
        from cast2md.db.repository import SettingsRepository

        try:
            cache_data = {
                "cached_at": datetime.now().isoformat(),
                "gpus": gpus,
            }
            with get_db() as conn:
                repo = SettingsRepository(conn)
                repo.set(self.GPU_CACHE_KEY, json.dumps(cache_data))
            logger.info(f"Cached {len(gpus)} GPU types")
        except Exception as e:
            logger.warning(f"Failed to cache GPU data: {e}")

    def _fetch_gpus_from_api(self) -> list[dict]:
        """Fetch GPU types with pricing from RunPod API (slow - use cache)."""
        runpod.api_key = self.settings.runpod_api_key
        try:
            gpus = runpod.get_gpus()
            result = []

            for gpu in gpus:
                gpu_id = gpu.get("id", "")
                display_name = gpu.get("displayName", gpu_id)
                memory_gb = gpu.get("memoryInGb", 0)

                # Skip if not in allowed list
                if not any(gpu_id.startswith(prefix) for prefix in self.ALLOWED_GPU_PREFIXES):
                    continue

                # Skip if insufficient VRAM
                if memory_gb < self.MIN_GPU_VRAM:
                    continue

                # Fetch detailed info including pricing
                try:
                    gpu_detail = runpod.get_gpu(gpu_id)
                    price_hr = gpu_detail.get("communityPrice") if gpu_detail else None
                except Exception:
                    price_hr = None

                # Skip if price exceeds threshold (or unknown)
                if price_hr is None or price_hr > self.MAX_GPU_PRICE:
                    continue

                result.append({
                    "id": gpu_id,
                    "display_name": display_name,
                    "memory_gb": memory_gb if memory_gb else None,
                    "price_hr": price_hr,
                })

            # Sort by price ascending (cheapest first for fallback)
            result.sort(key=lambda x: (x.get("price_hr") or 999, x["display_name"]))
            return result
        except Exception as e:
            logger.error(f"Failed to get RunPod GPU types: {e}")
            return []

    def get_setup_states(self) -> list[PodSetupState]:
        """Get all pod setup states (for status display)."""
        with self._lock:
            return list(self._setup_states.values())

    def get_setup_state(self, instance_id: str) -> PodSetupState | None:
        """Get setup state for a specific instance."""
        with self._lock:
            return self._setup_states.get(instance_id)

    def create_pod_async(self) -> str:
        """Start pod creation in background. Returns instance_id."""
        can_create, reason = self.can_create_pod()
        if not can_create:
            raise RuntimeError(reason)

        # Generate unique instance ID
        instance_id = secrets.token_hex(2)
        ts_hostname = f"{self.settings.runpod_ts_hostname}-{instance_id}"
        pod_name = f"cast2md-afterburner-{instance_id}"
        node_name = f"RunPod Afterburner {instance_id}"

        # Create initial state
        state = PodSetupState(
            instance_id=instance_id,
            pod_name=pod_name,
            ts_hostname=ts_hostname,
            node_name=node_name,
            phase=PodSetupPhase.CREATING,
            message="Starting pod creation...",
        )

        with self._lock:
            self._setup_states[instance_id] = state

        # Start background thread for setup
        thread = threading.Thread(
            target=self._create_and_setup_pod,
            args=(instance_id,),
            daemon=True,
        )
        thread.start()

        return instance_id

    def _update_state(self, instance_id: str, **kwargs: Any) -> None:
        """Update setup state (thread-safe)."""
        with self._lock:
            if instance_id in self._setup_states:
                state = self._setup_states[instance_id]
                for key, value in kwargs.items():
                    setattr(state, key, value)

    def _create_and_setup_pod(self, instance_id: str) -> None:
        """Full pod setup in background thread."""
        state = self._setup_states.get(instance_id)
        if not state:
            return

        try:
            # Ensure template exists
            template_id = self._ensure_template()
            if not template_id:
                self._update_state(instance_id, phase=PodSetupPhase.FAILED, error="Failed to create template")
                return

            # Create pod
            self._update_state(instance_id, message="Creating RunPod pod...")
            pod_id, gpu_type = self._create_pod(template_id, state.pod_name, state.ts_hostname)
            self._update_state(instance_id, pod_id=pod_id, gpu_type=gpu_type, phase=PodSetupPhase.STARTING, message="Waiting for pod to start...")

            # Wait for pod to be running
            self._wait_for_pod_running(pod_id)
            self._update_state(instance_id, phase=PodSetupPhase.CONNECTING, message="Waiting for Tailscale connection...")

            # Wait for Tailscale connection
            host_ip = self._wait_for_tailscale(state.ts_hostname)
            if not host_ip:
                raise RuntimeError("Pod failed to connect to Tailscale")
            self._update_state(instance_id, host_ip=host_ip, phase=PodSetupPhase.INSTALLING, message="Installing dependencies...")

            # SSH setup
            self._setup_pod_via_ssh(host_ip, state.node_name)
            self._update_state(instance_id, phase=PodSetupPhase.READY, message="Worker is running")

            logger.info(f"Pod {instance_id} ({pod_id}) setup complete")

        except Exception as e:
            logger.error(f"Pod {instance_id} setup failed: {e}")
            self._update_state(instance_id, phase=PodSetupPhase.FAILED, error=str(e))

    def _ensure_template(self) -> str | None:
        """Ensure the afterburner template exists. Returns template ID."""
        if self._template_id:
            return self._template_id

        runpod.api_key = self.settings.runpod_api_key
        client = httpx.Client(
            headers={"Authorization": f"Bearer {self.settings.runpod_api_key}"},
            timeout=30.0,
        )

        try:
            # Check if template exists
            response = client.get("https://rest.runpod.io/v1/templates")
            response.raise_for_status()
            templates = response.json()
            existing = next((t for t in templates if t.get("name") == self.TEMPLATE_NAME), None)

            if existing:
                self._template_id = existing["id"]
                return self._template_id

            # Create new template
            template_data = {
                "name": self.TEMPLATE_NAME,
                "imageName": self.settings.runpod_image_name,
                "dockerStartCmd": ["bash", "-c", self.STARTUP_SCRIPT],
                "containerDiskInGb": 20,
                "volumeInGb": 0,
                "ports": ["22/tcp"],
                "isPublic": False,
                "isServerless": False,
                "env": {
                    "TS_AUTH_KEY": "{{ RUNPOD_SECRET_ts_auth_key }}",
                    "TS_HOSTNAME": self.settings.runpod_ts_hostname,
                    "CAST2MD_SERVER_URL": self.settings.runpod_server_url,
                    "CAST2MD_SERVER_IP": self.settings.runpod_server_ip,
                    "GITHUB_REPO": self.settings.runpod_github_repo,
                },
                "readme": "cast2md Afterburner - On-demand GPU transcription worker",
            }

            response = client.post("https://rest.runpod.io/v1/templates", json=template_data)
            response.raise_for_status()
            result = response.json()
            self._template_id = result["id"]
            logger.info(f"Created RunPod template: {self._template_id}")
            return self._template_id

        except Exception as e:
            logger.error(f"Failed to ensure template: {e}")
            return None
        finally:
            client.close()

    def _create_pod(self, template_id: str, pod_name: str, ts_hostname: str) -> tuple[str, str]:
        """Create a RunPod pod. Returns (pod_id, gpu_type)."""
        runpod.api_key = self.settings.runpod_api_key

        # Build GPU fallback list
        gpu_types = [self.settings.runpod_gpu_type]
        fallbacks = [
            "NVIDIA GeForce RTX 4090",
            "NVIDIA GeForce RTX 3090",
            "NVIDIA RTX A4000",
            "NVIDIA RTX A5000",
            "NVIDIA GeForce RTX 4080",
        ]
        for fb in fallbacks:
            if fb not in gpu_types:
                gpu_types.append(fb)

        last_error = None
        for gpu_type in gpu_types:
            try:
                pod = runpod.create_pod(
                    name=pod_name,
                    template_id=template_id,
                    gpu_type_id=gpu_type,
                    cloud_type="ALL",
                    start_ssh=True,
                    support_public_ip=True,
                    env={"TS_HOSTNAME": ts_hostname},
                )
                logger.info(f"Created pod {pod['id']} ({pod_name}) with {gpu_type}")
                return pod["id"], gpu_type
            except Exception as e:
                error_msg = str(e).lower()
                if "resources" in error_msg or "not have" in error_msg:
                    logger.warning(f"{gpu_type} not available, trying next...")
                    last_error = e
                    continue
                raise

        raise RuntimeError(f"No GPU available. Last error: {last_error}")

    def _wait_for_pod_running(self, pod_id: str, timeout: int = 300) -> None:
        """Wait for pod to reach RUNNING status."""
        runpod.api_key = self.settings.runpod_api_key
        start_time = time.time()

        while time.time() - start_time < timeout:
            pod = runpod.get_pod(pod_id)
            if pod is None:
                time.sleep(5)
                continue

            status = pod.get("desiredStatus", "")
            runtime = pod.get("runtime") or {}

            if status == "RUNNING" and runtime:
                return

            if status in ("EXITED", "ERROR"):
                raise RuntimeError(f"Pod failed to start: {status}")

            time.sleep(5)

        raise RuntimeError("Timeout waiting for pod to be running")

    def _wait_for_tailscale(self, ts_hostname: str, timeout: int = 600) -> str | None:
        """Wait for pod to appear on Tailscale. Returns IP or None."""
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

                # Find matching candidates
                candidates = []
                for peer in peers.values():
                    hostname = peer.get("HostName", "")
                    online = peer.get("Online", False)
                    if hostname.startswith(ts_hostname) and online:
                        candidates.append(peer)

                # Sort by creation time (newest first)
                def get_created_time(p: dict) -> datetime:
                    created_str = p.get("Created", "")
                    if not created_str:
                        return datetime.min
                    try:
                        if created_str.endswith("Z"):
                            created_str = created_str[:-1] + "+00:00"
                        return datetime.fromisoformat(created_str)
                    except ValueError:
                        return datetime.min

                candidates.sort(key=get_created_time, reverse=True)

                # Try candidates
                for peer in candidates:
                    ip = peer.get("TailscaleIPs", ["?"])[0]
                    if ip == "?":
                        continue

                    # Verify SSH connectivity
                    ssh_result = subprocess.run(
                        ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", f"root@{ip}", "exit 0"],
                        capture_output=True,
                        timeout=10,
                    )
                    if ssh_result.returncode == 0:
                        return ip

            time.sleep(5)

        return None

    def _setup_pod_via_ssh(self, host_ip: str, node_name: str) -> None:
        """Install cast2md and dependencies on the pod via SSH."""

        def run_ssh(cmd: str, description: str, timeout: int = 300) -> str:
            ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=30", f"root@{host_ip}", cmd]
            try:
                result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"SSH command timed out: {description}")
            if result.returncode != 0:
                raise RuntimeError(f"SSH command failed ({description}): {result.stderr}")
            return result.stdout.strip()

        settings = self.settings
        server_url = self.get_effective_server_url()
        server_ip = self.get_effective_server_ip()

        # Add server to /etc/hosts
        server_host = server_url.replace("https://", "").replace("http://", "").split("/")[0]
        run_ssh(f"echo '{server_ip} {server_host}' >> /etc/hosts", "Adding server to /etc/hosts")

        # Install ffmpeg
        run_ssh("apt-get update -qq && apt-get install -y -qq ffmpeg > /dev/null 2>&1", "Installing ffmpeg")

        # Install cast2md
        run_ssh(
            f"pip install --no-cache-dir 'cast2md[node] @ git+https://github.com/{settings.runpod_github_repo}.git'",
            "Installing cast2md",
            timeout=600,
        )

        # Convert URL to HTTP:8000 for internal Tailscale traffic
        internal_url = server_url
        if internal_url.startswith("https://"):
            internal_url = "http://" + internal_url[8:]
        elif not internal_url.startswith("http://"):
            internal_url = "http://" + internal_url
        if ":8000" not in internal_url:
            internal_url = internal_url.rstrip("/") + ":8000"

        # Register node
        run_ssh(f"http_proxy=http://localhost:1055 cast2md node register --server '{internal_url}' --name '{node_name}'", "Registering node")

        # Start worker
        run_ssh(
            f"http_proxy=http://localhost:1055 WHISPER_MODEL={settings.runpod_whisper_model} nohup cast2md node start > /tmp/cast2md-node.log 2>&1 &",
            "Starting worker",
        )

        # Verify worker started
        time.sleep(3)
        run_ssh(
            "pgrep -f 'cast2md node' > /dev/null || (echo 'Worker not running!' && cat /tmp/cast2md-node.log && exit 1)",
            "Verifying worker",
        )

    def terminate_pod(self, pod_id: str) -> bool:
        """Terminate a specific pod."""
        if not self.is_available():
            return False

        runpod.api_key = self.settings.runpod_api_key
        try:
            runpod.terminate_pod(pod_id)
            logger.info(f"Terminated pod {pod_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to terminate pod {pod_id}: {e}")
            return False

    def terminate_all(self) -> int:
        """Terminate all afterburner pods. Returns count terminated."""
        pods = self.list_pods()
        count = 0
        for pod in pods:
            if self.terminate_pod(pod.id):
                count += 1
        return count

    def should_auto_scale(self, queue_depth: int) -> int:
        """Calculate how many pods to start based on queue depth.

        Returns number of pods to start (0 if none needed).
        """
        if not self.is_enabled():
            return 0
        if not self.settings.runpod_auto_scale:
            return 0

        threshold = self.settings.runpod_scale_threshold
        if queue_depth <= threshold:
            return 0

        current_pods = len(self.list_pods())
        creating_pods = len([s for s in self._setup_states.values() if s.phase not in (PodSetupPhase.READY, PodSetupPhase.FAILED)])
        max_pods = self.settings.runpod_max_pods

        # Calculate desired pods based on queue depth
        desired = min((queue_depth // threshold) * self.settings.runpod_pods_per_threshold, max_pods)
        new_pods_needed = max(0, desired - current_pods - creating_pods)

        return new_pods_needed

    def get_pod_ntfy_config(self) -> tuple[str | None, str | None]:
        """Get ntfy config suitable for pods (HTTP, IP-based).

        Returns (ntfy_url, ntfy_topic) or (None, None) if not available.
        """
        if not self.settings.ntfy_enabled or not self.settings.ntfy_topic:
            return None, None

        ntfy_url = self.settings.ntfy_url
        parsed = urlparse(ntfy_url)
        hostname = parsed.hostname

        if not hostname:
            return None, None

        try:
            ip = socket.gethostbyname(hostname)
        except socket.gaierror:
            return None, None

        # Reconstruct with IP and HTTP
        port = parsed.port
        if port:
            ntfy_url = f"http://{ip}:{port}"
        else:
            ntfy_url = f"http://{ip}"

        return ntfy_url, self.settings.ntfy_topic

    def cleanup_stale_states(self, max_age_hours: int = 24) -> int:
        """Remove stale setup states older than max_age_hours.

        Returns number of states removed.
        """
        cutoff = datetime.now().timestamp() - (max_age_hours * 3600)
        removed = 0

        with self._lock:
            to_remove = [
                instance_id
                for instance_id, state in self._setup_states.items()
                if state.started_at.timestamp() < cutoff and state.phase in (PodSetupPhase.READY, PodSetupPhase.FAILED)
            ]
            for instance_id in to_remove:
                del self._setup_states[instance_id]
                removed += 1

        return removed


# Singleton instance
_service: RunPodService | None = None


def get_runpod_service() -> RunPodService:
    """Get the RunPod service singleton."""
    global _service
    if _service is None:
        _service = RunPodService()
    return _service
