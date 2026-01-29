"""Application settings using Pydantic BaseSettings."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from cast2md import __version__

# Build list of env files (later files override earlier ones)
_env_files = [".env"]
_node_env = Path.home() / ".cast2md" / ".env"
if _node_env.exists():
    _env_files.append(str(_node_env))


class Settings(BaseSettings):
    """Application configuration with environment variable loading."""

    model_config = SettingsConfigDict(
        env_file=tuple(_env_files),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore DATABASE_URL and other unrecognized env vars
    )

    # Storage paths
    storage_path: Path = Path("./data/podcasts")
    temp_download_path: Path = Path("./data/temp")

    # Whisper configuration
    whisper_model: str = "large-v3-turbo"
    whisper_device: Literal["cpu", "cuda", "auto"] = "auto"
    whisper_compute_type: Literal["int8", "float16", "float32"] = "int8"
    whisper_backend: Literal["auto", "faster-whisper", "mlx"] = "auto"

    # Transcription backend: whisper (default) or parakeet (for GPU nodes)
    transcription_backend: Literal["whisper", "parakeet"] = "whisper"

    # Whisper chunking for memory efficiency (faster-whisper only)
    # Episodes longer than threshold are processed in chunks to avoid OOM
    whisper_chunk_threshold_minutes: int = 30  # Chunk episodes longer than this
    whisper_chunk_size_minutes: int = 30       # Size of each chunk

    # Download settings
    max_concurrent_downloads: int = 2
    max_transcript_download_workers: int = 4  # Parallel workers for fetching external transcripts
    max_retry_attempts: int = 3
    request_timeout: int = 30

    # Queue management
    stuck_threshold_minutes: int = 30  # Jobs running longer than this are considered stuck

    # Transcript discovery
    transcript_unavailable_age_days: int = 14  # Episodes older than this without external URLs marked unavailable
    transcript_retry_days: int = 14  # How long to retry external transcript downloads before giving up

    # HTTP client settings
    user_agent: str = f"cast2md/{__version__} (Podcast Transcription Service)"

    # iTunes search settings
    itunes_country: str = "de"  # ISO 3166-1 alpha-2 country code for iTunes store

    # Notifications (ntfy)
    ntfy_enabled: bool = False
    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""  # Required if enabled

    # Distributed transcription
    distributed_transcription_enabled: bool = False
    node_heartbeat_timeout_seconds: int = 60
    remote_job_timeout_minutes: int = 30

    # RunPod configuration (requires both tokens to be enabled)
    runpod_enabled: bool = False  # Master switch - must be True to use RunPod
    runpod_max_pods: int = 3  # Maximum concurrent pods
    runpod_auto_scale: bool = False  # Auto-start pods when queue grows
    runpod_scale_threshold: int = 10  # Start pod when queue > this
    runpod_pods_per_threshold: int = 1  # Pods to start per threshold crossed

    # RunPod credentials (from env only - not stored in DB)
    runpod_api_key: str = ""  # RunPod API key
    runpod_ts_auth_key: str = ""  # Tailscale auth key for pods

    # RunPod pod configuration
    runpod_gpu_type: str = "NVIDIA RTX A5000"
    # Comma-separated list of GPU types to exclude (e.g. RTX 4090 has CUDA issues with NeMo/Parakeet)
    runpod_blocked_gpus: str = "NVIDIA GeForce RTX 4090,NVIDIA GeForce RTX 4080,NVIDIA L4"
    runpod_whisper_model: str = "parakeet-tdt-0.6b-v3"
    runpod_image_name: str = "meltforce/cast2md-afterburner:cuda124"
    runpod_ts_hostname: str = "runpod-afterburner"  # Base hostname (instance ID appended)
    runpod_github_repo: str = "meltforce/cast2md"
    runpod_idle_timeout_minutes: int = 10  # Auto-terminate pods after idle for this many minutes (0 to disable)

    # Server connection (for pods to register)
    runpod_server_url: str = ""  # e.g., https://cast2md.example.ts.net
    runpod_server_ip: str = ""  # Tailscale IP (MagicDNS not available in pods)

    def ensure_directories(self) -> None:
        """Create required directories if they don't exist."""
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.temp_download_path.mkdir(parents=True, exist_ok=True)


# Cached settings instance
_settings: Settings | None = None


# Available transcription models for RunPod GPU workers
# Parakeet supports 25 European languages, Whisper supports 99+ languages
RUNPOD_TRANSCRIPTION_MODELS = [
    ("parakeet-tdt-0.6b-v3", "Parakeet TDT 0.6B v3 (fast, 25 EU languages)"),
    ("large-v3-turbo", "Whisper large-v3-turbo"),
    ("large-v3", "Whisper large-v3"),
    ("large-v2", "Whisper large-v2"),
    ("medium", "Whisper medium"),
    ("small", "Whisper small"),
]


# Node-specific settings - these come from env file only (not stored in DB).
# This includes sensitive credentials that shouldn't be in the database.
NODE_SPECIFIC_SETTINGS = frozenset({
    "runpod_api_key",
    "runpod_ts_auth_key",
})

# Default values for comparison (to detect env file overrides)
_DEFAULTS = {
    "whisper_model": "large-v3-turbo",
    "whisper_device": "auto",
    "whisper_compute_type": "int8",
    "whisper_backend": "auto",
    "transcription_backend": "whisper",
    "whisper_chunk_threshold_minutes": 30,
    "whisper_chunk_size_minutes": 30,
    "max_concurrent_downloads": 2,
    "max_transcript_download_workers": 4,
    "max_retry_attempts": 3,
    "request_timeout": 30,
    "stuck_threshold_minutes": 30,
    "itunes_country": "de",
    "ntfy_enabled": False,
    "ntfy_url": "https://ntfy.sh",
    "ntfy_topic": "",
    "distributed_transcription_enabled": False,
    "node_heartbeat_timeout_seconds": 60,
    "remote_job_timeout_minutes": 30,
    # RunPod settings
    "runpod_enabled": False,
    "runpod_max_pods": 3,
    "runpod_auto_scale": False,
    "runpod_scale_threshold": 10,
    "runpod_pods_per_threshold": 1,
    "runpod_api_key": "",
    "runpod_ts_auth_key": "",
    "runpod_gpu_type": "NVIDIA RTX A5000",
    "runpod_blocked_gpus": "NVIDIA GeForce RTX 4090,NVIDIA GeForce RTX 4080,NVIDIA L4",
    "runpod_whisper_model": "parakeet-tdt-0.6b-v3",
    "runpod_image_name": "meltforce/cast2md-afterburner:cuda124",
    "runpod_ts_hostname": "runpod-afterburner",
    "runpod_github_repo": "meltforce/cast2md",
    "runpod_idle_timeout_minutes": 10,
    "runpod_server_url": "",
    "runpod_server_ip": "",
}


def get_settings() -> Settings:
    """Get settings instance, applying database overrides if available."""
    global _settings
    if _settings is None:
        _settings = Settings()
        _apply_db_overrides()
    return _settings


def _apply_db_overrides() -> None:
    """Apply settings overrides from database (if available).

    All server settings can be configured via the UI and stored in the database.
    Remote nodes are separate installations with their own Settings class and
    .env files - they don't share this database.

    Environment variables always take precedence over database values.
    This allows switching between bare metal (STORAGE_PATH=/mnt/nas/cast2md)
    and Docker (STORAGE_PATH=/app/data/podcasts) without touching the database.
    """
    global _settings
    if _settings is None:
        return

    try:
        # Only import here to avoid circular imports
        import os

        from cast2md.db.connection import get_db
        from cast2md.db.repository import SettingsRepository

        with get_db() as conn:
            repo = SettingsRepository(conn)
            overrides = repo.get_all()

            for key, value in overrides.items():
                if not hasattr(_settings, key):
                    continue
                # Skip if env var is explicitly set (env wins over DB)
                if key in NODE_SPECIFIC_SETTINGS or key.upper() in os.environ:
                    continue
                current_value = getattr(_settings, key)
                field_type = type(current_value)
                try:
                    if field_type == int:
                        setattr(_settings, key, int(value))
                    elif field_type == bool:
                        setattr(_settings, key, value.lower() in ("true", "1", "yes"))
                    elif isinstance(current_value, Path):
                        setattr(_settings, key, Path(value))
                    else:
                        setattr(_settings, key, value)
                except (ValueError, TypeError):
                    pass  # Skip invalid values
    except Exception:
        # Database might not be initialized yet
        pass


def reload_settings() -> Settings:
    """Force reload of settings (clears cache and reapplies db overrides)."""
    global _settings
    _settings = Settings()
    _apply_db_overrides()
    return _settings


def get_setting_source(key: str, current_value, db_value: str | None) -> str:
    """Determine the actual source of a setting value.

    Args:
        key: Setting key name.
        current_value: Current effective value from Settings.
        db_value: Value stored in database (or None).

    Returns:
        One of: "env_file", "database", "default"
    """
    import os

    # Env vars always win over DB (node-specific or explicitly set)
    env_set = key in NODE_SPECIFIC_SETTINGS or key.upper() in os.environ
    if env_set:
        default = _DEFAULTS.get(key)
        if hasattr(current_value, "__fspath__"):
            current_value = str(current_value)
        if current_value != default:
            return "env_file"
        return "default"

    # For other settings, check if DB override is applied
    if db_value is not None:
        return "database"

    # Check if value differs from default (meaning it's from env file)
    default = _DEFAULTS.get(key)
    if default is not None:
        if hasattr(current_value, "__fspath__"):
            current_value = str(current_value)
        if current_value != default:
            return "env_file"

    return "default"
