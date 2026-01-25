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
    persistent: bool = False  # Dev mode: don't auto-terminate, allow code updates


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

    # Flag to track if DB states have been loaded
    _db_loaded: bool = False

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
        self._db_loaded = False

    def _ensure_db_loaded(self) -> None:
        """Load setup states from DB on first access."""
        if self._db_loaded:
            return

        try:
            from cast2md.db.connection import get_db
            from cast2md.db.repository import PodSetupStateRepository

            with get_db() as conn:
                repo = PodSetupStateRepository(conn)
                rows = repo.get_all()

                with self._lock:
                    for row in rows:
                        state = PodSetupState(
                            instance_id=row.instance_id,
                            pod_id=row.pod_id,
                            pod_name=row.pod_name,
                            ts_hostname=row.ts_hostname,
                            node_name=row.node_name,
                            gpu_type=row.gpu_type,
                            phase=PodSetupPhase(row.phase),
                            message=row.message,
                            started_at=row.started_at,
                            error=row.error,
                            host_ip=row.host_ip,
                            persistent=row.persistent,
                        )
                        self._setup_states[row.instance_id] = state

            self._db_loaded = True
            if rows:
                logger.info(f"Loaded {len(rows)} pod setup state(s) from database")

            # Clean up unreachable pods in background (always run on startup)
            thread = threading.Thread(target=self._cleanup_unreachable_pods, daemon=True)
            thread.start()
        except Exception as e:
            logger.warning(f"Failed to load pod setup states from DB: {e}")
            self._db_loaded = True  # Don't retry on every access

    def _persist_state(self, state: PodSetupState) -> None:
        """Persist a setup state to the database."""
        try:
            from cast2md.db.connection import get_db
            from cast2md.db.repository import PodSetupStateRepository, PodSetupStateRow

            row = PodSetupStateRow(
                instance_id=state.instance_id,
                pod_id=state.pod_id,
                pod_name=state.pod_name,
                ts_hostname=state.ts_hostname,
                node_name=state.node_name,
                gpu_type=state.gpu_type,
                phase=state.phase.value,
                message=state.message,
                started_at=state.started_at,
                error=state.error,
                host_ip=state.host_ip,
                persistent=state.persistent,
            )
            with get_db() as conn:
                repo = PodSetupStateRepository(conn)
                repo.upsert(row)
        except Exception as e:
            logger.warning(f"Failed to persist pod setup state: {e}")

    def _delete_persisted_state(self, instance_id: str) -> None:
        """Delete a setup state from the database."""
        try:
            from cast2md.db.connection import get_db
            from cast2md.db.repository import PodSetupStateRepository

            with get_db() as conn:
                repo = PodSetupStateRepository(conn)
                repo.delete(instance_id)
        except Exception as e:
            logger.warning(f"Failed to delete persisted pod setup state: {e}")

    def _cleanup_unreachable_pods(self) -> None:
        """Terminate pods that are running but not reachable via Tailscale.

        Called on startup after loading persisted states. Pods that were
        mid-setup when the server restarted may be running on RunPod but
        never completed Tailscale setup, making them useless.
        """
        if not self.is_available():
            return

        try:
            # Give pods a moment to appear on Tailscale
            time.sleep(10)

            # Get running pods from RunPod
            running_pods = self.list_pods()
            if not running_pods:
                return

            # Get online Tailscale hosts
            result = subprocess.run(
                ["tailscale", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return

            status = json.loads(result.stdout)
            peers = status.get("Peer", {})

            # Build set of online afterburner hostnames
            online_hostnames = set()
            for peer in peers.values():
                hostname = peer.get("HostName", "")
                if hostname.startswith("runpod-afterburner") and peer.get("Online", False):
                    online_hostnames.add(hostname)

            # Check each running pod
            for pod in running_pods:
                # Extract instance_id from pod name (e.g., "cast2md-afterburner-1fd6" -> "1fd6")
                parts = pod.name.split("-")
                if len(parts) < 3:
                    continue
                instance_id = parts[-1]
                expected_hostname = f"runpod-afterburner-{instance_id}"

                if expected_hostname not in online_hostnames:
                    logger.warning(
                        f"Pod {pod.id} ({pod.name}) is running but not reachable via Tailscale - terminating"
                    )
                    self.terminate_pod(pod.id)
                    # Also clean up the setup state if it exists
                    if instance_id in self._setup_states:
                        with self._lock:
                            if instance_id in self._setup_states:
                                del self._setup_states[instance_id]
                        self._delete_persisted_state(instance_id)

        except Exception as e:
            logger.error(f"Failed to cleanup unreachable pods: {e}")

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
        self._ensure_db_loaded()
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

        # Count running pods from RunPod API
        running_pods = self.list_pods()
        running_pod_ids = {p.id for p in running_pods}

        # Count setup states that aren't yet in the running list (avoid double-counting)
        creating = len([
            s for s in self._setup_states.values()
            if s.phase not in (PodSetupPhase.READY, PodSetupPhase.FAILED)
            and (s.pod_id is None or s.pod_id not in running_pod_ids)
        ])

        total = len(running_pods) + creating

        if total >= self.settings.runpod_max_pods:
            return False, f"Max pods ({self.settings.runpod_max_pods}) reached ({len(running_pods)} running, {creating} creating)"

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
        self._ensure_db_loaded()
        with self._lock:
            return list(self._setup_states.values())

    def get_setup_state(self, instance_id: str) -> PodSetupState | None:
        """Get setup state for a specific instance."""
        self._ensure_db_loaded()
        with self._lock:
            return self._setup_states.get(instance_id)

    def create_pod_async(self, persistent: bool = False) -> str:
        """Start pod creation in background. Returns instance_id.

        Args:
            persistent: If True, pod won't be auto-terminated and allows code updates.
                       Use for development/debugging.
        """
        self._ensure_db_loaded()
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
            persistent=persistent,
        )

        with self._lock:
            self._setup_states[instance_id] = state

        # Persist to database
        self._persist_state(state)

        # Start background thread for setup
        thread = threading.Thread(
            target=self._create_and_setup_pod,
            args=(instance_id,),
            daemon=True,
        )
        thread.start()

        return instance_id

    def _update_state(self, instance_id: str, **kwargs: Any) -> None:
        """Update setup state (thread-safe) and persist to DB."""
        state = None
        with self._lock:
            if instance_id in self._setup_states:
                state = self._setup_states[instance_id]
                for key, value in kwargs.items():
                    setattr(state, key, value)
        # Persist outside the lock to avoid holding it during DB operations
        if state:
            self._persist_state(state)

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

            # Record pod run in database
            self._record_pod_run(instance_id, pod_id, state.pod_name, gpu_type, state.started_at)

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

        # Build GPU fallback list: selected GPU first, then others sorted by price
        selected_gpu = self.settings.runpod_gpu_type
        gpu_types = [selected_gpu]

        # Add cached GPUs (sorted by price) as fallbacks
        cached_gpus = self.get_available_gpus()
        for gpu in cached_gpus:
            if gpu["id"] not in gpu_types:
                gpu_types.append(gpu["id"])

        # Hardcoded fallback if cache is empty
        if len(gpu_types) == 1:
            for fb in ["NVIDIA GeForce RTX 4090", "NVIDIA GeForce RTX 3090", "NVIDIA RTX A4000"]:
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

                    # Verify SSH connectivity using Tailscale SSH
                    ssh_result = subprocess.run(
                        ["tailscale", "ssh", f"root@{ip}", "exit", "0"],
                        capture_output=True,
                        timeout=15,
                    )
                    if ssh_result.returncode == 0:
                        return ip

            time.sleep(5)

        return None

    def _setup_pod_via_ssh(self, host_ip: str, node_name: str) -> None:
        """Install cast2md and dependencies on the pod via Tailscale SSH."""
        from cast2md.services.pod_setup import PodSetupConfig, setup_pod

        def run_ssh(cmd: str, description: str, timeout: int = 300) -> str:
            # Use Tailscale SSH (pods don't have regular sshd)
            ssh_cmd = ["tailscale", "ssh", f"root@{host_ip}", cmd]
            try:
                result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"SSH command timed out: {description}")
            if result.returncode != 0:
                raise RuntimeError(f"SSH command failed ({description}): {result.stderr}")
            return result.stdout.strip()

        # Use shared setup logic
        config = PodSetupConfig(
            server_url=self.get_effective_server_url(),
            server_ip=self.get_effective_server_ip(),
            node_name=node_name,
            model=self.settings.runpod_whisper_model,
            github_repo=self.settings.runpod_github_repo,
        )
        setup_pod(config, run_ssh)

        # Verify worker started
        time.sleep(3)
        run_ssh(
            "pgrep -f 'cast2md node' > /dev/null || (echo 'Worker not running!' && cat /tmp/cast2md-node.log && exit 1)",
            "Verifying worker",
        )

    def _get_gpu_price(self, gpu_type: str) -> float | None:
        """Get the hourly price for a GPU type from cache."""
        cached_gpus = self.get_available_gpus()
        for gpu in cached_gpus:
            if gpu["id"] == gpu_type:
                return gpu.get("price_hr")
        return None

    def _record_pod_run(
        self,
        instance_id: str,
        pod_id: str,
        pod_name: str,
        gpu_type: str,
        started_at: datetime,
    ) -> None:
        """Record a new pod run in the database."""
        from cast2md.db.connection import get_db
        from cast2md.db.repository import PodRunRepository

        gpu_price = self._get_gpu_price(gpu_type)
        try:
            with get_db() as conn:
                repo = PodRunRepository(conn)
                repo.create(
                    instance_id=instance_id,
                    pod_id=pod_id,
                    pod_name=pod_name,
                    gpu_type=gpu_type,
                    gpu_price_hr=gpu_price,
                    started_at=started_at,
                )
            logger.info(f"Recorded pod run for {pod_id} ({gpu_type} @ ${gpu_price}/hr)")
        except Exception as e:
            logger.error(f"Failed to record pod run: {e}")

    def _end_pod_run(self, pod_id: str) -> None:
        """Mark a pod run as ended in the database."""
        from cast2md.db.connection import get_db
        from cast2md.db.repository import PodRunRepository

        try:
            with get_db() as conn:
                repo = PodRunRepository(conn)
                repo.end_run(pod_id)
            logger.info(f"Ended pod run for {pod_id}")
        except Exception as e:
            logger.error(f"Failed to end pod run: {e}")

    def terminate_pod(self, pod_id: str) -> bool:
        """Terminate a specific pod and clean up its node registration."""
        if not self.is_available():
            return False

        runpod.api_key = self.settings.runpod_api_key
        try:
            # Find the instance_id from setup states to get the node name
            instance_id = None
            for state in self._setup_states.values():
                if state.pod_id == pod_id:
                    instance_id = state.instance_id
                    break

            runpod.terminate_pod(pod_id)
            self._end_pod_run(pod_id)

            # Delete the node registration (it will never reconnect)
            if instance_id:
                node_name = f"RunPod Afterburner {instance_id}"
                self._delete_node_by_name(node_name)

            logger.info(f"Terminated pod {pod_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to terminate pod {pod_id}: {e}")
            return False

    def _delete_node_by_name(self, name: str) -> bool:
        """Delete a node from the database by name."""
        from cast2md.db.connection import get_db
        from cast2md.db.repository import TranscriberNodeRepository

        try:
            with get_db() as conn:
                repo = TranscriberNodeRepository(conn)
                if repo.delete_by_name(name):
                    logger.info(f"Deleted node '{name}'")
                    return True
        except Exception as e:
            logger.error(f"Failed to delete node '{name}': {e}")
        return False

    def terminate_all(self) -> int:
        """Terminate all afterburner pods and clean up nodes. Returns count terminated."""
        pods = self.list_pods()
        count = 0
        for pod in pods:
            if self.terminate_pod(pod.id):
                count += 1

        # Also clean up any orphaned RunPod nodes
        self.cleanup_orphaned_nodes()
        return count

    def cleanup_orphaned_nodes(self) -> int:
        """Delete offline RunPod Afterburner nodes that don't have matching pods."""
        self._ensure_db_loaded()
        from cast2md.db.connection import get_db
        from cast2md.db.repository import TranscriberNodeRepository

        try:
            with get_db() as conn:
                repo = TranscriberNodeRepository(conn)
                nodes = repo.get_all()

                # Get current pod instance IDs
                current_instance_ids = set(self._setup_states.keys())

                count = 0
                for node in nodes:
                    # Only clean up RunPod Afterburner nodes
                    if not node.name.startswith("RunPod Afterburner"):
                        continue
                    # Extract instance_id from name (e.g., "RunPod Afterburner 6b9f" -> "6b9f")
                    parts = node.name.split()
                    if len(parts) >= 3:
                        instance_id = parts[-1]
                        # Delete if not in current setup states and offline
                        if instance_id not in current_instance_ids and node.status == "offline":
                            if repo.delete(node.id):
                                logger.info(f"Cleaned up orphaned node: {node.name}")
                                count += 1
                return count
        except Exception as e:
            logger.error(f"Failed to cleanup orphaned nodes: {e}")
            return 0

    def update_pod_code(self, instance_id: str) -> bool:
        """Update cast2md code on a running pod (for development).

        Stops the worker, reinstalls from git, and restarts.
        Only works on pods in READY state.
        """
        state = self.get_setup_state(instance_id)
        if not state:
            logger.error(f"No setup state found for {instance_id}")
            return False
        if state.phase != PodSetupPhase.READY:
            logger.error(f"Pod {instance_id} is not ready (phase: {state.phase})")
            return False
        if not state.host_ip:
            logger.error(f"Pod {instance_id} has no host IP")
            return False

        def run_ssh(cmd: str, description: str, timeout: int = 300) -> str:
            ssh_cmd = ["tailscale", "ssh", f"root@{state.host_ip}", cmd]
            try:
                result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"SSH command timed out: {description}")
            if result.returncode != 0:
                raise RuntimeError(f"SSH command failed ({description}): {result.stderr}")
            return result.stdout.strip()

        settings = self.settings
        try:
            # Stop the worker
            run_ssh("pkill -f 'cast2md node' || true", "Stopping worker")

            # Reinstall from git
            run_ssh(
                f"pip install --no-cache-dir 'cast2md[node] @ git+https://github.com/{settings.runpod_github_repo}.git'",
                "Updating cast2md",
                timeout=600,
            )

            # Determine backend from model
            model = settings.runpod_whisper_model
            is_parakeet = "parakeet" in model.lower()
            backend_env = "TRANSCRIPTION_BACKEND=parakeet" if is_parakeet else ""

            # Restart worker
            run_ssh(
                f"http_proxy=http://localhost:1055 {backend_env} WHISPER_MODEL={model} "
                "nohup cast2md node start > /tmp/cast2md-node.log 2>&1 &",
                "Restarting worker",
            )

            logger.info(f"Updated code on pod {instance_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update pod code: {e}")
            return False

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

        self._ensure_db_loaded()
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
        self._ensure_db_loaded()
        cutoff = datetime.now().timestamp() - (max_age_hours * 3600)
        to_remove = []

        with self._lock:
            to_remove = [
                instance_id
                for instance_id, state in self._setup_states.items()
                if state.started_at.timestamp() < cutoff
                and state.phase in (PodSetupPhase.READY, PodSetupPhase.FAILED)
                and not state.persistent
            ]
            for instance_id in to_remove:
                del self._setup_states[instance_id]

        # Delete from DB outside the lock
        for instance_id in to_remove:
            self._delete_persisted_state(instance_id)

        return len(to_remove)

    def dismiss_setup_state(self, instance_id: str) -> bool:
        """Dismiss/clear a setup state (typically a failed one).

        Returns True if state was found and removed.
        """
        self._ensure_db_loaded()
        found = False
        with self._lock:
            if instance_id in self._setup_states:
                del self._setup_states[instance_id]
                found = True

        if found:
            self._delete_persisted_state(instance_id)
        return found

    def cleanup_orphaned_states(self) -> int:
        """Remove failed states whose pods no longer exist.

        Returns number of states removed.
        """
        self._ensure_db_loaded()
        active_pod_ids = {p.id for p in self.list_pods()}
        to_remove = []

        with self._lock:
            to_remove = [
                instance_id
                for instance_id, state in self._setup_states.items()
                if state.phase == PodSetupPhase.FAILED
                and state.pod_id is not None
                and state.pod_id not in active_pod_ids
            ]
            for instance_id in to_remove:
                del self._setup_states[instance_id]

        # Delete from DB outside the lock
        for instance_id in to_remove:
            self._delete_persisted_state(instance_id)

        return len(to_remove)

    def get_pod_runs(self, limit: int = 20) -> list[dict]:
        """Get recent pod runs with cost info."""
        from cast2md.db.connection import get_db
        from cast2md.db.repository import PodRunRepository

        try:
            with get_db() as conn:
                repo = PodRunRepository(conn)
                return repo.get_recent(limit)
        except Exception as e:
            logger.error(f"Failed to get pod runs: {e}")
            return []

    def get_pod_run_stats(self, days: int = 30) -> dict:
        """Get aggregate stats for pod runs."""
        from cast2md.db.connection import get_db
        from cast2md.db.repository import PodRunRepository

        try:
            with get_db() as conn:
                repo = PodRunRepository(conn)
                return repo.get_stats(days)
        except Exception as e:
            logger.error(f"Failed to get pod run stats: {e}")
            return {"total_runs": 0, "total_jobs": 0, "total_cost": 0, "total_hours": 0}


# Singleton instance
_service: RunPodService | None = None


def get_runpod_service() -> RunPodService:
    """Get the RunPod service singleton."""
    global _service
    if _service is None:
        _service = RunPodService()
    return _service
