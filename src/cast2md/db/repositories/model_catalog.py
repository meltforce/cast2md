"""Repositories for the selectable transcription model catalogs."""

from dataclasses import dataclass
from datetime import datetime

from cast2md.constants import RUNPOD_TRANSCRIPTION_MODELS
from cast2md.db.sql import Connection, execute


@dataclass
class WhisperModel:
    """A whisper model configuration."""

    id: str
    backend: str
    hf_repo: str | None
    description: str | None
    size_mb: int | None
    is_enabled: bool

    @classmethod
    def from_row(cls, row) -> "WhisperModel":
        """Create from database row."""
        return cls(
            id=row[0],
            backend=row[1],
            hf_repo=row[2],
            description=row[3],
            size_mb=row[4],
            is_enabled=bool(row[5]),
        )


class WhisperModelRepository:
    """Repository for whisper model configurations."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def get_all(self, enabled_only: bool = True) -> list[WhisperModel]:
        """Get all models."""
        if enabled_only:
            cursor = execute(
                self.conn,
                "SELECT id, backend, hf_repo, description, size_mb, is_enabled FROM whisper_models WHERE is_enabled = TRUE ORDER BY id",
            )
        else:
            cursor = execute(
                self.conn,
                "SELECT id, backend, hf_repo, description, size_mb, is_enabled FROM whisper_models ORDER BY id",
            )
        return [WhisperModel.from_row(row) for row in cursor.fetchall()]

    def get_by_id(self, model_id: str) -> WhisperModel | None:
        """Get a model by ID."""
        cursor = execute(
            self.conn,
            "SELECT id, backend, hf_repo, description, size_mb, is_enabled FROM whisper_models WHERE id = %s",
            (model_id,),
        )
        row = cursor.fetchone()
        return WhisperModel.from_row(row) if row else None

    def upsert(
        self,
        model_id: str,
        backend: str,
        hf_repo: str | None = None,
        description: str | None = None,
        size_mb: int | None = None,
        is_enabled: bool = True,
    ) -> None:
        """Insert or update a model."""
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            INSERT INTO whisper_models (id, backend, hf_repo, description, size_mb, is_enabled, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                backend = EXCLUDED.backend, hf_repo = EXCLUDED.hf_repo,
                description = EXCLUDED.description, size_mb = EXCLUDED.size_mb,
                is_enabled = EXCLUDED.is_enabled
            """,
            (model_id, backend, hf_repo, description, size_mb, is_enabled, now),
        )
        self.conn.commit()

    def delete(self, model_id: str) -> bool:
        """Delete a model."""
        cursor = execute(self.conn, "DELETE FROM whisper_models WHERE id = %s", (model_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def delete_all(self) -> int:
        """Delete every model, so seed_defaults can repopulate the table.

        Returns:
            Number of models deleted.
        """
        cursor = execute(self.conn, "DELETE FROM whisper_models")
        self.conn.commit()
        return cursor.rowcount

    def seed_defaults(self) -> int:
        """Seed the default models if table is empty."""
        cursor = execute(self.conn, "SELECT COUNT(*) FROM whisper_models")
        if cursor.fetchone()[0] > 0:
            return 0

        default_models = [
            ("tiny", "both", "mlx-community/whisper-tiny", "Fastest, least accurate", 75),
            ("tiny.en", "both", "mlx-community/whisper-tiny.en-mlx", "English-only tiny", 75),
            ("base", "both", "mlx-community/whisper-base-mlx", "Fast, good accuracy", 142),
            ("base.en", "both", "mlx-community/whisper-base.en-mlx", "English-only base", 142),
            ("small", "both", "mlx-community/whisper-small-mlx", "Balanced speed/accuracy", 466),
            ("small.en", "both", "mlx-community/whisper-small.en-mlx", "English-only small", 466),
            ("medium", "both", "mlx-community/whisper-medium-mlx", "High accuracy", 1500),
            (
                "medium.en",
                "both",
                "mlx-community/whisper-medium.en-mlx",
                "English-only medium",
                1500,
            ),
            (
                "large-v2",
                "both",
                "mlx-community/whisper-large-v2-mlx",
                "Previous best accuracy",
                3000,
            ),
            ("large-v3", "both", "mlx-community/whisper-large-v3-mlx", "Best accuracy", 3000),
            (
                "large-v3-turbo",
                "both",
                "mlx-community/whisper-large-v3-turbo",
                "Fast large model",
                1600,
            ),
        ]

        now = datetime.now().isoformat()
        for model_id, backend, hf_repo, description, size_mb in default_models:
            execute(
                self.conn,
                """
                INSERT INTO whisper_models (id, backend, hf_repo, description, size_mb, is_enabled, created_at)
                VALUES (%s, %s, %s, %s, %s, TRUE, %s)
                """,
                (model_id, backend, hf_repo, description, size_mb, now),
            )
        self.conn.commit()
        return len(default_models)


@dataclass
class RunPodModel:
    """A RunPod transcription model configuration."""

    id: str
    display_name: str
    backend: str  # 'whisper' or 'parakeet'
    is_enabled: bool
    sort_order: int

    @classmethod
    def from_row(cls, row) -> "RunPodModel":
        """Create from database row."""
        return cls(
            id=row[0],
            display_name=row[1],
            backend=row[2],
            is_enabled=bool(row[3]),
            sort_order=row[4],
        )


class RunPodModelRepository:
    """Repository for RunPod transcription model configurations."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def get_all(self, enabled_only: bool = True) -> list[RunPodModel]:
        """Get all models, ordered by sort_order."""
        if enabled_only:
            cursor = execute(
                self.conn,
                "SELECT id, display_name, backend, is_enabled, sort_order FROM runpod_models WHERE is_enabled = TRUE ORDER BY sort_order, id",
            )
        else:
            cursor = execute(
                self.conn,
                "SELECT id, display_name, backend, is_enabled, sort_order FROM runpod_models ORDER BY sort_order, id",
            )
        return [RunPodModel.from_row(row) for row in cursor.fetchall()]

    def get_by_id(self, model_id: str) -> RunPodModel | None:
        """Get a model by ID."""
        cursor = execute(
            self.conn,
            "SELECT id, display_name, backend, is_enabled, sort_order FROM runpod_models WHERE id = %s",
            (model_id,),
        )
        row = cursor.fetchone()
        return RunPodModel.from_row(row) if row else None

    def upsert(
        self,
        model_id: str,
        display_name: str,
        backend: str = "whisper",
        is_enabled: bool = True,
        sort_order: int = 100,
    ) -> None:
        """Insert or update a model."""
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            INSERT INTO runpod_models (id, display_name, backend, is_enabled, sort_order, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                display_name = EXCLUDED.display_name, backend = EXCLUDED.backend,
                is_enabled = EXCLUDED.is_enabled, sort_order = EXCLUDED.sort_order
            """,
            (model_id, display_name, backend, is_enabled, sort_order, now),
        )
        self.conn.commit()

    def delete(self, model_id: str) -> bool:
        """Delete a model."""
        cursor = execute(self.conn, "DELETE FROM runpod_models WHERE id = %s", (model_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def delete_all(self) -> int:
        """Delete every model, so seed_defaults can repopulate the table.

        Returns:
            Number of models deleted.
        """
        cursor = execute(self.conn, "DELETE FROM runpod_models")
        self.conn.commit()
        return cursor.rowcount

    def seed_defaults(self) -> int:
        """Seed the default models if table is empty."""
        cursor = execute(self.conn, "SELECT COUNT(*) FROM runpod_models")
        if cursor.fetchone()[0] > 0:
            return 0

        now = datetime.now().isoformat()
        for idx, (model_id, display_name) in enumerate(RUNPOD_TRANSCRIPTION_MODELS):
            # Determine backend from model_id
            backend = "parakeet" if "parakeet" in model_id else "whisper"
            execute(
                self.conn,
                """
                INSERT INTO runpod_models (id, display_name, backend, is_enabled, sort_order, created_at)
                VALUES (%s, %s, %s, TRUE, %s, %s)
                """,
                (model_id, display_name, backend, idx * 10, now),
            )
        self.conn.commit()
        return len(RUNPOD_TRANSCRIPTION_MODELS)
