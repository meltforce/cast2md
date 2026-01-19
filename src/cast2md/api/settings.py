"""Settings API endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel

from cast2md.config.settings import get_settings
from cast2md.db.connection import get_db
from cast2md.db.repository import SettingsRepository, WhisperModelRepository

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _get_configurable_settings() -> dict:
    """Get configurable settings with dynamic model options."""
    # Get whisper models from database
    with get_db() as conn:
        model_repo = WhisperModelRepository(conn)
        # Seed defaults if empty
        model_repo.seed_defaults()
        models = model_repo.get_all(enabled_only=True)

    model_options = [m.id for m in models]

    return {
        "max_concurrent_downloads": {
            "type": "int",
            "label": "Download Workers",
            "description": "Number of concurrent download workers (requires restart)",
            "min": 1,
            "max": 10,
        },
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
    }


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
        env_value = getattr(env_settings, key, None)
        db_value = db_overrides.get(key)

        # Convert Path objects to strings
        if hasattr(env_value, "__fspath__"):
            env_value = str(env_value)

        settings[key] = {
            "value": db_value if db_value is not None else env_value,
            "default": env_value,
            "source": "database" if db_value is not None else "environment",
            "has_override": db_value is not None,
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
        conn.execute("DELETE FROM whisper_models")
        conn.commit()
        # Re-seed defaults
        repo = WhisperModelRepository(conn)
        count = repo.seed_defaults()

    return MessageResponse(message=f"Models reset to {count} defaults.")
