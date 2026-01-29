"""RunPod GPU worker pod management service."""

import ipaddress
import json
import logging
import secrets
import socket
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
    persistent: bool = False  # Dev mode: don't auto-terminate
    setup_token: str = ""  # One-time token for pod self-setup authentication


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

    # Template name for self-setup pods (v2 to avoid conflict with CLI afterburner's template)
    TEMPLATE_NAME = "cast2md-afterburner-v2"

    # Flag to track if DB states have been loaded
    _db_loaded: bool = False

    # Self-setup startup script: pod handles all setup and reports progress to server
    STARTUP_SCRIPT = '''#!/bin/bash
set -e

echo "=== Afterburner Self-Setup $(date) ==="

: "${TS_AUTH_KEY:?TS_AUTH_KEY is required}"
: "${TS_HOSTNAME:=runpod-afterburner}"
: "${CAST2MD_SERVER_IP:?CAST2MD_SERVER_IP is required}"
: "${INSTANCE_ID:?INSTANCE_ID is required}"
: "${SETUP_TOKEN:?SETUP_TOKEN is required}"

echo "Config: TS_HOSTNAME=$TS_HOSTNAME INSTANCE_ID=$INSTANCE_ID"

# --- Tailscale setup ---
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

# --- /etc/hosts for MagicDNS workaround ---
SERVER_HOST=$(echo "$CAST2MD_SERVER_URL" | sed "s|https://||;s|http://||;s|/.*||")
grep -q "$SERVER_HOST" /etc/hosts || echo "$CAST2MD_SERVER_IP $SERVER_HOST" >> /etc/hosts

# --- Helper to report progress ---
report_progress() {
    local phase="$1"
    local message="$2"
    local host_ip
    host_ip=$(tailscale ip -4 2>/dev/null || echo "")
    local error="${3:-}"
    local json
    if [ -n "$error" ]; then
        json=$(printf \'{"phase": "%s", "message": "%s", "host_ip": "%s", "error": "%s"}\' "$phase" "$message" "$host_ip" "$error")
    else
        json=$(printf \'{"phase": "%s", "message": "%s", "host_ip": "%s"}\' "$phase" "$message" "$host_ip")
    fi
    curl -s -X POST "http://${CAST2MD_SERVER_IP}:8000/api/runpod/pods/${INSTANCE_ID}/setup-progress" \
        -H "Content-Type: application/json" \
        -H "X-Setup-Token: ${SETUP_TOKEN}" \
        -d "$json" \
        -x http://localhost:1055 || true
}

# --- Report Tailscale connected ---
report_progress "connecting" "Tailscale connected"

# --- Install ffmpeg ---
which ffmpeg > /dev/null || (apt-get update -qq && apt-get install -y -qq ffmpeg > /dev/null 2>&1)

# --- Install NeMo toolkit for Parakeet (if not pre-installed) ---
if [ "$TRANSCRIPTION_BACKEND" = "parakeet" ]; then
    python -c "import nemo" 2>/dev/null || {
        report_progress "installing" "Installing NeMo toolkit..."
        pip install --no-cache-dir "nemo_toolkit[asr]"
    }
fi

# --- Install cast2md ---
report_progress "installing" "Installing cast2md..."
pip install --no-cache-dir "cast2md[node] @ git+https://github.com/${GITHUB_REPO}.git"

# --- GPU smoke test (Parakeet only) ---
if [ "$TRANSCRIPTION_BACKEND" = "parakeet" ]; then
    report_progress "installing" "Running GPU validation..."
    timeout 120 python -c "
from cast2md.transcription.service import TranscriptionService
import tempfile, numpy as np, soundfile as sf
svc = TranscriptionService()
model = svc._get_parakeet_model(\'nvidia/parakeet-tdt-0.6b-v3\')
silence = np.zeros(16000, dtype=np.float32)
f = tempfile.NamedTemporaryFile(suffix=\'.wav\', delete=False)
sf.write(f.name, silence, 16000)
model.transcribe([f.name], timestamps=True)
print(\'GPU validation passed\')
" || {
        report_progress "failed" "GPU validation failed" "GPU smoke test failed"
        tail -f /dev/null
    }
fi

# --- Register node ---
report_progress "installing" "Registering node..."
http_proxy=http://localhost:1055 cast2md node register \
    --server "http://${CAST2MD_SERVER_IP}:8000" --name "$NODE_NAME"

# --- Start worker ---
report_progress "installing" "Starting worker..."
http_proxy=http://localhost:1055 \
    TRANSCRIPTION_BACKEND=$TRANSCRIPTION_BACKEND \
    NODE_IDLE_TIMEOUT_MINUTES=$NODE_IDLE_TIMEOUT_MINUTES \
    NODE_PERSISTENT=$NODE_PERSISTENT \
    NODE_MAX_CONSECUTIVE_FAILURES=${NODE_MAX_CONSECUTIVE_FAILURES:-3} \
    WHISPER_MODEL=$WHISPER_MODEL \
    nohup cast2md node start > /tmp/cast2md-node.log 2>&1 &

sleep 3

# Verify worker started
if ! pgrep -f "cast2md node" > /dev/null; then
    report_progress "failed" "Worker failed to start" "$(tail -20 /tmp/cast2md-node.log 2>/dev/null)"
    tail -f /dev/null
fi

# --- Watchdog + terminate script (non-persistent only) ---
if [ "$NODE_PERSISTENT" != "1" ]; then
    # Build terminate script with baked-in values
    # RUNPOD_POD_ID is auto-set by RunPod, RUNPOD_API_KEY is from pod env
    printf '#!/bin/bash\necho "Terminating pod via RunPod API..." >> /tmp/cast2md-node.log\ncurl -s -X POST https://api.runpod.io/graphql -H "Content-Type: application/json" -H "Authorization: Bearer %s" -d '"'"'{"query": "mutation { podTerminate(input: {podId: \\"%s\\"}) }"}'"'"' >> /tmp/cast2md-node.log 2>&1\n' "$RUNPOD_API_KEY" "$RUNPOD_POD_ID" > /tmp/terminate-pod.sh
    chmod +x /tmp/terminate-pod.sh

    # Watchdog monitors worker process and terminates pod when it exits
    nohup bash -c "sleep 10; while pgrep -f 'cast2md node' > /dev/null; do sleep 5; done; echo 'Worker exited, terminating pod...' >> /tmp/cast2md-node.log; /tmp/terminate-pod.sh" > /tmp/watchdog.log 2>&1 &
fi

# --- Report ready ---
report_progress "ready" "Worker is running"

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
                            setup_token=row.setup_token,
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
                setup_token=state.setup_token,
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
        """Clean up pods stuck in setup phases without progress.

        Called on startup after loading persisted states. Pods that were
        mid-setup when the server restarted may be running on RunPod but
        never completed setup, making them useless.

        Checks for pods stuck in CONNECTING/INSTALLING phase for >15 min
        without progress, and verifies with RunPod API that the pod still exists.
        """
        if not self.is_available():
            return

        try:
            # Give pods a moment to start reporting
            time.sleep(10)

            # Get running pods from RunPod
            running_pods = self.list_pods()
            running_pod_ids = {p.id for p in running_pods}

            # Check setup states for stuck pods
            stuck_instance_ids = []
            with self._lock:
                for instance_id, state in self._setup_states.items():
                    # Skip terminal states
                    if state.phase in (PodSetupPhase.READY, PodSetupPhase.FAILED):
                        continue

                    # Check if pod still exists in RunPod
                    if state.pod_id and state.pod_id not in running_pod_ids:
                        logger.warning(
                            f"Pod {instance_id} ({state.pod_id}) no longer exists in RunPod - cleaning up"
                        )
                        stuck_instance_ids.append(instance_id)
                        continue

                    # Check if stuck (>15 min in non-terminal phase)
                    age_minutes = (datetime.now() - state.started_at).total_seconds() / 60
                    if age_minutes > 15:
                        logger.warning(
                            f"Pod {instance_id} stuck in {state.phase.value} for {age_minutes:.0f} min - terminating"
                        )
                        stuck_instance_ids.append(instance_id)

            # Terminate stuck pods
            for instance_id in stuck_instance_ids:
                state = self._setup_states.get(instance_id)
                if state and state.pod_id:
                    try:
                        self.terminate_pod(state.pod_id)
                    except Exception as e:
                        logger.error(f"Failed to terminate stuck pod {instance_id}: {e}")
                # Clean up state
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
        """Get this server's Tailscale hostname and IP from configuration.

        Returns (url, ip) or (None, None) if not configured.
        """
        url = self.settings.runpod_server_url or None
        ip = self.settings.runpod_server_ip or None
        return url, ip

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

    # GPUs suitable for transcription (good price/performance, sufficient VRAM)
    # Note: RTX 40xx consumer GPUs and L4 have CUDA compatibility issues with NeMo/Parakeet
    # These are filtered against runpod_blocked_gpus setting at runtime
    ALLOWED_GPU_PREFIXES = [
        "NVIDIA GeForce RTX 3090",
        "NVIDIA GeForce RTX 3080",
        "NVIDIA RTX A4000",
        "NVIDIA RTX A4500",
        "NVIDIA RTX A5000",
        "NVIDIA RTX A6000",
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

    def reconcile_setup_states(self) -> None:
        """Remove setup states for pods that no longer exist in RunPod.

        This handles the case where pods are terminated externally (via RunPod API
        directly, e.g., by the watchdog) and our tracked states become stale.
        """
        if not self.is_available():
            return

        try:
            # Get actual pods from RunPod
            actual_pods = self.list_pods()
            actual_pod_ids = {p.id for p in actual_pods}

            # Find stale states (ready/failed states whose pod_id no longer exists)
            stale_instance_ids = []
            with self._lock:
                for instance_id, state in self._setup_states.items():
                    # Only check states that have a pod_id and are in a terminal phase
                    # Don't touch states still being set up (creating, starting, connecting, installing)
                    if state.pod_id and state.phase in (PodSetupPhase.READY, PodSetupPhase.FAILED):
                        if state.pod_id not in actual_pod_ids:
                            stale_instance_ids.append(instance_id)
                            logger.info(f"Reconcile: pod {state.pod_id} ({instance_id}) no longer exists in RunPod")

            # Remove stale states
            for instance_id in stale_instance_ids:
                with self._lock:
                    if instance_id in self._setup_states:
                        del self._setup_states[instance_id]
                self._delete_persisted_state(instance_id)
                logger.info(f"Reconcile: removed stale setup state {instance_id}")

        except Exception as e:
            logger.warning(f"Failed to reconcile setup states: {e}")

    def get_setup_states(self) -> list[PodSetupState]:
        """Get all pod setup states (for status display).

        Also reconciles states with RunPod to clean up terminated pods.
        """
        self._ensure_db_loaded()
        # Reconcile before returning - cleans up externally terminated pods
        self.reconcile_setup_states()
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

        # Generate unique instance ID and setup token
        instance_id = secrets.token_hex(2)
        setup_token = secrets.token_urlsafe(32)
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
            setup_token=setup_token,
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
        """Create pod and wait for it to self-setup (background thread).

        The pod's startup script handles all setup and reports progress back
        via the setup-progress API endpoint. This thread just monitors for
        completion or timeout.
        """
        state = self._setup_states.get(instance_id)
        if not state:
            return

        try:
            # Ensure template exists
            template_id = self._ensure_template()
            if not template_id:
                self._update_state(instance_id, phase=PodSetupPhase.FAILED, error="Failed to create template")
                return

            # Create pod (with env vars for self-setup)
            self._update_state(instance_id, message="Creating RunPod pod...")
            pod_id, gpu_type = self._create_pod(
                template_id, state.pod_name, state.ts_hostname, instance_id,
            )
            self._update_state(
                instance_id, pod_id=pod_id, gpu_type=gpu_type,
                phase=PodSetupPhase.STARTING, message="Waiting for pod to start...",
            )

            # Wait for pod to reach RUNNING status (via RunPod API)
            self._wait_for_pod_running(pod_id)
            self._update_state(
                instance_id, phase=PodSetupPhase.CONNECTING,
                message="Pod running, waiting for self-setup...",
            )

            # Wait for pod to report "ready" or "failed" via setup-progress API
            # The pod's startup script handles everything and calls back
            setup_timeout = 900  # 15 minutes
            start_time = time.time()

            while time.time() - start_time < setup_timeout:
                with self._lock:
                    current_state = self._setup_states.get(instance_id)

                if not current_state:
                    raise RuntimeError("Setup state disappeared")

                if current_state.phase == PodSetupPhase.READY:
                    logger.info(f"Pod {instance_id} ({pod_id}) self-setup complete")
                    return

                if current_state.phase == PodSetupPhase.FAILED:
                    logger.error(f"Pod {instance_id} self-setup failed: {current_state.error}")
                    return  # State already set to FAILED by the progress endpoint

                time.sleep(5)

            # Timeout - mark as failed and terminate
            self._update_state(
                instance_id, phase=PodSetupPhase.FAILED,
                error="Setup timed out (15 min) - pod did not report ready",
            )
            logger.error(f"Pod {instance_id} setup timed out, terminating")
            try:
                self.terminate_pod(pod_id)
            except Exception:
                pass

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

    def _create_pod(
        self, template_id: str, pod_name: str, ts_hostname: str, instance_id: str,
    ) -> tuple[str, str]:
        """Create a RunPod pod with self-setup env vars. Returns (pod_id, gpu_type)."""
        runpod.api_key = self.settings.runpod_api_key

        # Parse blocked GPUs (comma-separated list)
        blocked_gpus = set(
            gpu.strip()
            for gpu in self.settings.runpod_blocked_gpus.split(",")
            if gpu.strip()
        )

        def is_blocked(gpu_id: str) -> bool:
            """Check if GPU is in blocklist."""
            return any(blocked in gpu_id for blocked in blocked_gpus)

        if blocked_gpus:
            logger.info(f"GPU blocklist: {blocked_gpus}")

        # Build GPU fallback list: selected GPU first, then others sorted by price
        selected_gpu = self.settings.runpod_gpu_type
        if is_blocked(selected_gpu):
            logger.warning(f"Preferred GPU {selected_gpu} is in blocklist, skipping")
            gpu_types = []
        else:
            gpu_types = [selected_gpu]

        # Add cached GPUs (sorted by price) as fallbacks, excluding blocked ones
        cached_gpus = self.get_available_gpus()
        for gpu in cached_gpus:
            if gpu["id"] not in gpu_types and not is_blocked(gpu["id"]):
                gpu_types.append(gpu["id"])

        # Hardcoded fallback if list is empty (excluding blocked GPUs)
        if len(gpu_types) == 0:
            for fb in ["NVIDIA RTX A5000", "NVIDIA RTX A6000", "NVIDIA GeForce RTX 3090", "NVIDIA RTX A4000"]:
                if fb not in gpu_types and not is_blocked(fb):
                    gpu_types.append(fb)

        # Get setup token from state
        state = self._setup_states.get(instance_id)
        setup_token = state.setup_token if state else ""

        # Determine transcription backend from model
        model = self.settings.runpod_whisper_model
        is_parakeet = "parakeet" in model.lower()
        backend = "parakeet" if is_parakeet else "whisper"

        # Per-pod env vars for self-setup
        pod_env = {
            "TS_HOSTNAME": ts_hostname,
            "INSTANCE_ID": instance_id,
            "SETUP_TOKEN": setup_token,
            "TRANSCRIPTION_BACKEND": backend,
            "WHISPER_MODEL": model,
            "NODE_NAME": f"RunPod Afterburner {instance_id}",
            "NODE_IDLE_TIMEOUT_MINUTES": str(self.settings.runpod_idle_timeout_minutes),
            "NODE_PERSISTENT": "1" if (state and state.persistent) else "0",
            "NODE_MAX_CONSECUTIVE_FAILURES": "3",
            "RUNPOD_API_KEY": self.settings.runpod_api_key,
        }

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
                    env=pod_env,
                )
                pod_id = pod["id"]

                # Query RunPod to get the ACTUAL allocated GPU (not just what we requested)
                actual_gpu = gpu_type  # fallback to requested
                try:
                    pod_details = runpod.get_pod(pod_id)
                    if pod_details:
                        machine = pod_details.get("machine") or {}
                        actual_gpu = machine.get("gpuDisplayName") or gpu_type
                        if actual_gpu != gpu_type:
                            logger.warning(f"GPU mismatch! Requested {gpu_type}, got {actual_gpu}")
                except Exception as e:
                    logger.warning(f"Could not verify GPU type: {e}")

                # If RunPod gave us a blocked GPU, terminate immediately and try next
                if is_blocked(actual_gpu):
                    logger.error(f"RunPod allocated blocked GPU {actual_gpu}! Terminating pod {pod_id}")
                    try:
                        runpod.terminate_pod(pod_id)
                    except Exception:
                        pass
                    last_error = RuntimeError(f"RunPod allocated blocked GPU: {actual_gpu}")
                    continue

                logger.info(f"Created pod {pod_id} ({pod_name}) with {actual_gpu} (requested: {gpu_type})")
                return pod_id, actual_gpu
            except Exception as e:
                error_msg = str(e).lower()
                if "resources" in error_msg or "not have" in error_msg:
                    logger.warning(f"{gpu_type} not available, trying next...")
                    last_error = e
                    continue
                raise

        raise RuntimeError(f"No GPU available. Last error: {last_error}")

    def _wait_for_pod_running(self, pod_id: str, timeout: int = 600) -> None:
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

        self._ensure_db_loaded()
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

            # Clean up node and setup state
            if instance_id:
                node_name = f"RunPod Afterburner {instance_id}"
                self._delete_node_by_name(node_name)
                # Remove setup state from memory and DB
                with self._lock:
                    if instance_id in self._setup_states:
                        del self._setup_states[instance_id]
                self._delete_persisted_state(instance_id)

            logger.info(f"Terminated pod {pod_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to terminate pod {pod_id}: {e}")
            return False

    def terminate_by_instance_id(self, instance_id: str) -> bool:
        """Terminate a pod by its instance_id.

        Used by node workers to request termination before exiting.
        This allows the server to clean up state atomically.

        Args:
            instance_id: The instance ID (e.g., "a3f2")

        Returns:
            True if pod was terminated, False otherwise
        """
        state = self.get_setup_state(instance_id)
        if not state:
            logger.warning(f"No setup state found for instance {instance_id}")
            return False

        if not state.pod_id:
            logger.warning(f"No pod_id in setup state for instance {instance_id}")
            return False

        # Release any jobs claimed by this node before terminating
        self._release_node_jobs(state.node_name)

        logger.info(f"Terminating pod for instance {instance_id} (pod_id={state.pod_id})")
        return self.terminate_pod(state.pod_id)

    def _release_node_jobs(self, node_name: str) -> int:
        """Release all jobs claimed by a node back to queued status.

        Args:
            node_name: The node name to release jobs for

        Returns:
            Number of jobs released
        """
        from cast2md.db.connection import get_db
        from cast2md.db.repository import JobRepository, TranscriberNodeRepository

        released = 0
        try:
            with get_db() as conn:
                node_repo = TranscriberNodeRepository(conn)
                job_repo = JobRepository(conn)

                node = node_repo.get_by_name(node_name)
                if not node:
                    return 0

                jobs = job_repo.get_jobs_by_node(node.id)
                for job in jobs:
                    if job.status.value in ("running", "queued"):
                        job_repo.unclaim_job(job.id)
                        released += 1
                        logger.info(f"Released job {job.id} from node {node_name}")

        except Exception as e:
            logger.warning(f"Failed to release jobs for node {node_name}: {e}")

        return released

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

    def set_persistent(self, instance_id: str, persistent: bool) -> bool:
        """Set the persistent (dev mode) flag for a pod.

        When persistent=True, the pod won't be auto-terminated and allows code updates.
        Returns True if the state was found and updated.
        """
        self._ensure_db_loaded()
        with self._lock:
            state = self._setup_states.get(instance_id)
            if not state:
                return False
            state.persistent = persistent

        # Update in database
        try:
            from cast2md.db.connection import get_db
            from cast2md.db.repository import PodSetupStateRepository

            with get_db() as conn:
                repo = PodSetupStateRepository(conn)
                repo.set_persistent(instance_id, persistent)
            logger.info(f"Set pod {instance_id} persistent={persistent}")
            return True
        except Exception as e:
            logger.error(f"Failed to set persistent for {instance_id}: {e}")
            return False

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
