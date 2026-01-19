"""Application settings using Pydantic BaseSettings."""

from functools import lru_cache
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

    # Download settings
    max_concurrent_downloads: int = 2
    max_retry_attempts: int = 3
    request_timeout: int = 30

    def ensure_directories(self) -> None:
        """Create required directories if they don't exist."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.temp_download_path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
