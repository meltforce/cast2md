"""Settings API endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel

import os

from cast2md.config.settings import (
    get_settings,
    get_setting_source,
    reload_settings,
    NODE_SPECIFIC_SETTINGS,
)
from cast2md.db.connection import get_db
from cast2md.db.repository import RunPodModelRepository, SettingsRepository, WhisperModelRepository
from cast2md.notifications.ntfy import send_notification, NotificationType

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _get_configurable_settings() -> dict:
    """Get configurable settings with dynamic model options."""
    # Get whisper models from database
    with get_db() as conn:
        model_repo = WhisperModelRepository(conn)
        # Seed defaults if empty
        model_repo.seed_defaults()
        models = model_repo.get_all(enabled_only=True)

        # Get RunPod models from database
        runpod_model_repo = RunPodModelRepository(conn)
        runpod_model_repo.seed_defaults()
        runpod_models = runpod_model_repo.get_all(enabled_only=True)

    model_options = [m.id for m in models]
    runpod_model_options = [m.id for m in runpod_models]

    # Order matters for UI layout (2 items per row)
    # Row 1: Whisper Model, Whisper Backend
    # Row 2: Download Workers (single)
    # Row 3: Storage Path, Temp Download Path
    # Row 4+: Notification settings
    settings = {
        "whisper_model": {
            "type": "select",
            "label": "Whisper Model",
            "description": "Transcription model size (requires restart)",
            "options": model_options if model_options else ["base"],
        },
        "whisper_backend": {
            "type": "select",
            "label": "Whisper Backend",
            "description": "Transcription backend (requires restart)",
            "options": ["auto", "faster-whisper", "mlx"],
        },
        "max_concurrent_downloads": {
            "type": "int",
            "label": "Audio Download Workers",
            "description": "Concurrent audio download workers (requires restart)",
            "min": 1,
            "max": 10,
        },
        "max_transcript_download_workers": {
            "type": "int",
            "label": "Transcript Download Workers",
            "description": "Concurrent transcript fetch workers (requires restart)",
            "min": 1,
            "max": 20,
        },
        "stuck_threshold_minutes": {
            "type": "int",
            "label": "Stuck Job Threshold (minutes)",
            "description": "Jobs running longer than this are considered stuck",
            "min": 5,
            "max": 120,
        },
        "transcript_unavailable_age_days": {
            "type": "int",
            "label": "Transcript Unavailable Age (days)",
            "description": "Episodes older than this without external transcript URLs are marked unavailable",
            "min": 7,
            "max": 365,
        },
        "storage_path": {
            "type": "path",
            "label": "Storage Path",
            "description": "Path for storing podcast audio and transcripts",
        },
        "temp_download_path": {
            "type": "path",
            "label": "Temp Download Path",
            "description": "Path for temporary downloads",
        },
        "ntfy_enabled": {
            "type": "bool",
            "label": "Enable Notifications",
            "description": "Send notifications via ntfy on completion/failure",
            "full_width": True,
        },
        "ntfy_url": {
            "type": "text",
            "label": "ntfy Server URL",
            "description": "ntfy server URL (default: https://ntfy.sh)",
        },
        "ntfy_topic": {
            "type": "text",
            "label": "ntfy Topic",
            "description": "Topic name for notifications (required if enabled)",
        },
        "distributed_transcription_enabled": {
            "type": "bool",
            "label": "Enable Distributed Transcription",
            "description": "Allow remote nodes to process transcription jobs",
            "full_width": True,
        },
        "node_heartbeat_timeout_seconds": {
            "type": "int",
            "label": "Node Heartbeat Timeout (seconds)",
            "description": "Mark nodes offline after this many seconds without heartbeat",
            "min": 30,
            "max": 300,
        },
        "remote_job_timeout_minutes": {
            "type": "int",
            "label": "Remote Job Timeout (minutes)",
            "description": "Reclaim jobs from nodes after this many minutes",
            "min": 5,
            "max": 120,
        },
        "itunes_country": {
            "type": "text",
            "label": "iTunes Country",
            "description": "ISO country code for iTunes search (e.g., de, us, gb)",
        },
        # RunPod settings - managed on /admin/runpod page, not shown on settings page
        "runpod_enabled": {
            "type": "bool",
            "label": "Enable RunPod",
            "description": "Master switch for RunPod GPU workers",
        },
        "runpod_max_pods": {
            "type": "int",
            "label": "Max Pods",
            "description": "Maximum concurrent GPU pods",
            "min": 1,
            "max": 10,
        },
        "runpod_auto_scale": {
            "type": "bool",
            "label": "Auto-scale",
            "description": "Automatically start pods when queue grows",
        },
        "runpod_scale_threshold": {
            "type": "int",
            "label": "Scale Threshold",
            "description": "Start pods when queue exceeds this count",
            "min": 1,
            "max": 100,
        },
        "runpod_gpu_type": {
            "type": "text",
            "label": "GPU Type",
            "description": "Preferred GPU for pods",
        },
        "runpod_whisper_model": {
            "type": "select",
            "label": "Pod Transcription Model",
            "description": "Transcription model for GPU pods",
            "options": runpod_model_options if runpod_model_options else ["parakeet-tdt-0.6b-v3"],
        },
    }

    return settings


class SettingsResponse(BaseModel):
    """Response with all settings."""

    settings: dict
    configurable: dict


class UpdateSettingsRequest(BaseModel):
    """Request to update settings."""

    settings: dict[str, str]


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str


@router.get("", response_model=SettingsResponse)
def get_all_settings():
    """Get all current settings with their values and metadata."""
    env_settings = get_settings()
    configurable = _get_configurable_settings()

    with get_db() as conn:
        repo = SettingsRepository(conn)
        db_overrides = repo.get_all()

    # Build settings dict with current values and source
    settings = {}
    for key, meta in configurable.items():
        # Get the current effective value
        current_value = getattr(env_settings, key, None)
        db_value = db_overrides.get(key)

        # Convert Path objects to strings for display
        display_value = current_value
        if hasattr(display_value, "__fspath__"):
            display_value = str(display_value)

        # Determine actual source of the value
        source = get_setting_source(key, current_value, db_value)

        # Field is locked when env var overrides DB
        env_override = key in NODE_SPECIFIC_SETTINGS or key.upper() in os.environ

        settings[key] = {
            "value": display_value,
            "db_value": db_value,  # What's stored in DB (may be ignored)
            "source": source,
            "env_override": env_override,
            **meta,
        }

    return SettingsResponse(
        settings=settings,
        configurable=configurable,
    )


@router.put("", response_model=MessageResponse)
def update_settings(request: UpdateSettingsRequest):
    """Update settings (stored as database overrides)."""
    configurable = _get_configurable_settings()

    with get_db() as conn:
        repo = SettingsRepository(conn)

        for key, value in request.settings.items():
            if key not in configurable:
                continue

            # Validate the value based on type
            meta = configurable[key]
            if meta["type"] == "int":
                try:
                    int_val = int(value)
                    if "min" in meta and int_val < meta["min"]:
                        continue
                    if "max" in meta and int_val > meta["max"]:
                        continue
                except ValueError:
                    continue
            elif meta["type"] == "select":
                if value not in meta["options"]:
                    continue
            elif meta["type"] == "bool":
                # Normalize boolean values
                value = "true" if value.lower() in ("true", "1", "yes") else "false"

            repo.set(key, str(value))

    return MessageResponse(message="Settings updated. Some changes require a restart.")


@router.delete("/{key}", response_model=MessageResponse)
def reset_setting(key: str):
    """Reset a setting to its default value."""
    configurable = _get_configurable_settings()
    if key not in configurable:
        return MessageResponse(message=f"Unknown setting: {key}")

    with get_db() as conn:
        repo = SettingsRepository(conn)
        repo.delete(key)

    return MessageResponse(message=f"Setting '{key}' reset to default.")


@router.delete("", response_model=MessageResponse)
def reset_all_settings():
    """Reset all settings to defaults."""
    configurable = _get_configurable_settings()
    with get_db() as conn:
        repo = SettingsRepository(conn)
        for key in configurable:
            repo.delete(key)

    return MessageResponse(message="All settings reset to defaults.")


# Whisper Models API

class WhisperModelResponse(BaseModel):
    """Response for a whisper model."""

    id: str
    backend: str
    hf_repo: str | None
    description: str | None
    size_mb: int | None
    is_enabled: bool


class WhisperModelsListResponse(BaseModel):
    """Response with all whisper models."""

    models: list[WhisperModelResponse]


class AddModelRequest(BaseModel):
    """Request to add a custom model."""

    id: str
    backend: str = "both"
    hf_repo: str | None = None
    description: str | None = None
    size_mb: int | None = None


@router.get("/models", response_model=WhisperModelsListResponse)
def list_models(include_disabled: bool = False):
    """List all available whisper models."""
    with get_db() as conn:
        repo = WhisperModelRepository(conn)
        repo.seed_defaults()
        models = repo.get_all(enabled_only=not include_disabled)

    return WhisperModelsListResponse(
        models=[
            WhisperModelResponse(
                id=m.id,
                backend=m.backend,
                hf_repo=m.hf_repo,
                description=m.description,
                size_mb=m.size_mb,
                is_enabled=m.is_enabled,
            )
            for m in models
        ]
    )


@router.post("/models", response_model=MessageResponse)
def add_model(request: AddModelRequest):
    """Add a custom whisper model."""
    with get_db() as conn:
        repo = WhisperModelRepository(conn)
        repo.upsert(
            model_id=request.id,
            backend=request.backend,
            hf_repo=request.hf_repo,
            description=request.description,
            size_mb=request.size_mb,
            is_enabled=True,
        )

    return MessageResponse(message=f"Model '{request.id}' added.")


@router.delete("/models/{model_id}", response_model=MessageResponse)
def delete_model(model_id: str):
    """Delete a whisper model."""
    with get_db() as conn:
        repo = WhisperModelRepository(conn)
        if repo.delete(model_id):
            return MessageResponse(message=f"Model '{model_id}' deleted.")
        return MessageResponse(message=f"Model '{model_id}' not found.")


@router.post("/models/reset", response_model=MessageResponse)
def reset_models():
    """Reset models to defaults (removes all custom models)."""
    with get_db() as conn:
        # Clear all models
        cursor = conn.cursor()
        cursor.execute("DELETE FROM whisper_models")
        conn.commit()
        # Re-seed defaults
        repo = WhisperModelRepository(conn)
        count = repo.seed_defaults()

    return MessageResponse(message=f"Models reset to {count} defaults.")


@router.post("/notifications/test", response_model=MessageResponse)
def test_notification():
    """Send a test notification to verify ntfy configuration."""
    # Reload settings to pick up any recent changes
    reload_settings()

    settings = get_settings()

    if not settings.ntfy_enabled:
        return MessageResponse(message="Notifications are disabled. Enable them in settings first.")

    if not settings.ntfy_topic:
        return MessageResponse(message="No ntfy topic configured. Set a topic in settings first.")

    success = send_notification(
        NotificationType.TRANSCRIPTION_COMPLETE,
        title="Test Notification",
        message="If you see this, notifications are working!",
        priority=3,
        tags=["white_check_mark", "test_tube"],
    )

    if success:
        return MessageResponse(message="Test notification sent successfully!")
    else:
        return MessageResponse(message="Failed to send notification. Check server logs for details.")
