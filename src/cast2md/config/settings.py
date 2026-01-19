"""Application settings using Pydantic BaseSettings."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration with environment variable loading."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_path: Path = Path("./data/cast2md.db")

    # Storage paths
    storage_path: Path = Path("./data/podcasts")
    temp_download_path: Path = Path("./data/temp")

    # Whisper configuration
    whisper_model: str = "base"
    whisper_device: Literal["cpu", "cuda", "auto"] = "auto"
    whisper_compute_type: Literal["int8", "float16", "float32"] = "int8"
    whisper_backend: Literal["auto", "faster-whisper", "mlx"] = "auto"

    # Download settings
    max_concurrent_downloads: int = 2
    max_retry_attempts: int = 3
    request_timeout: int = 30

    # Notifications (ntfy)
    ntfy_enabled: bool = False
    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""  # Required if enabled

    def ensure_directories(self) -> None:
        """Create required directories if they don't exist."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.temp_download_path.mkdir(parents=True, exist_ok=True)


# Cached settings instance
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get settings instance, applying database overrides if available."""
    global _settings
    if _settings is None:
        _settings = Settings()
        _apply_db_overrides()
    return _settings


def _apply_db_overrides() -> None:
    """Apply settings overrides from database (if available)."""
    global _settings
    if _settings is None:
        return

    try:
        # Only import here to avoid circular imports
        from cast2md.db.connection import get_db
        from cast2md.db.repository import SettingsRepository

        with get_db() as conn:
            repo = SettingsRepository(conn)
            overrides = repo.get_all()

            for key, value in overrides.items():
                if hasattr(_settings, key):
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
