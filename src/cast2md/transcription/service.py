"""Transcription service supporting faster-whisper and mlx-whisper backends."""

import logging
import platform
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cast2md.config.settings import get_settings

logger = logging.getLogger(__name__)
from cast2md.db.connection import get_db
from cast2md.db.models import Episode, EpisodeStatus, Feed
from cast2md.db.repository import EpisodeRepository
from cast2md.storage.filesystem import ensure_podcast_directories, get_transcript_path
from cast2md.transcription.preprocessing import preprocess_audio


@dataclass
class TranscriptSegment:
    """A segment of transcribed text."""

    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    """Complete transcription result."""

    segments: list[TranscriptSegment]
    language: str
    language_probability: float

    @property
    def full_text(self) -> str:
        """Get the full transcript as a single string."""
        return " ".join(seg.text.strip() for seg in self.segments)

    def to_markdown(self, title: str = "", include_timestamps: bool = True) -> str:
        """Convert transcript to markdown format.

        Args:
            title: Optional title for the document.
            include_timestamps: Whether to include timestamps.

        Returns:
            Markdown formatted transcript.
        """
        lines = []

        if title:
            lines.append(f"# {title}")
            lines.append("")

        lines.append(f"*Language: {self.language} ({self.language_probability:.1%} confidence)*")
        lines.append("")

        if include_timestamps:
            for seg in self.segments:
                timestamp = self._format_timestamp(seg.start)
                lines.append(f"**[{timestamp}]** {seg.text.strip()}")
                lines.append("")
        else:
            # Group into paragraphs
            paragraph = []
            for seg in self.segments:
                text = seg.text.strip()
                paragraph.append(text)
                # Start new paragraph on sentence-ending punctuation
                if text and text[-1] in ".!?":
                    lines.append(" ".join(paragraph))
                    lines.append("")
                    paragraph = []

            if paragraph:
                lines.append(" ".join(paragraph))
                lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        """Format seconds as MM:SS or HH:MM:SS."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"


def _is_apple_silicon() -> bool:
    """Check if running on Apple Silicon."""
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def _get_backend() -> str:
    """Determine which backend to use."""
    settings = get_settings()
    backend = settings.whisper_backend

    if backend == "auto":
        if _is_apple_silicon():
            # Check if mlx-whisper is available
            try:
                import mlx_whisper
                return "mlx"
            except ImportError:
                logger.info("mlx-whisper not installed, falling back to faster-whisper")
                return "faster-whisper"
        return "faster-whisper"

    return backend


class TranscriptionService:
    """Thread-safe singleton transcription service with lazy model loading."""

    _instance: Optional["TranscriptionService"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "TranscriptionService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._model = None
                    cls._instance._model_lock = threading.Lock()
                    cls._instance._backend = None
        return cls._instance

    @property
    def model(self):
        """Lazy-load the Whisper model."""
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    self._load_model()
        return self._model

    @property
    def backend(self) -> str:
        """Get the active backend name."""
        if self._backend is None:
            self._backend = _get_backend()
        return self._backend

    def _load_model(self) -> None:
        """Load the Whisper model based on settings."""
        settings = get_settings()
        backend = self.backend

        if backend == "mlx":
            # mlx-whisper doesn't need a model object, it loads per-call
            # but we'll store the model name for transcribe()
            logger.info(f"Using mlx-whisper backend with model: {settings.whisper_model}")
            self._model = {"backend": "mlx", "model": settings.whisper_model}
        else:
            from faster_whisper import WhisperModel
            logger.info(f"Using faster-whisper backend with model: {settings.whisper_model}")
            self._model = WhisperModel(
                settings.whisper_model,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
            )

    def transcribe(self, audio_path: Path) -> TranscriptResult:
        """Transcribe an audio file.

        Args:
            audio_path: Path to the audio file.

        Returns:
            TranscriptResult with segments and metadata.
        """
        # Preprocess audio (currently passthrough)
        processed_path = preprocess_audio(audio_path)

        if self.backend == "mlx":
            return self._transcribe_mlx(processed_path)
        else:
            return self._transcribe_faster_whisper(processed_path)

    def _transcribe_faster_whisper(self, audio_path: Path) -> TranscriptResult:
        """Transcribe using faster-whisper backend."""
        segments_iter, info = self.model.transcribe(
            str(audio_path),
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
            ),
        )

        segments = [
            TranscriptSegment(
                start=seg.start,
                end=seg.end,
                text=seg.text,
            )
            for seg in segments_iter
        ]

        return TranscriptResult(
            segments=segments,
            language=info.language,
            language_probability=info.language_probability,
        )

    def _transcribe_mlx(self, audio_path: Path) -> TranscriptResult:
        """Transcribe using mlx-whisper backend."""
        import mlx_whisper

        model_name = self.model["model"]

        # mlx-whisper uses HuggingFace model names
        model_map = {
            "tiny": "mlx-community/whisper-tiny",
            "tiny.en": "mlx-community/whisper-tiny.en-mlx",
            "base": "mlx-community/whisper-base-mlx",
            "base.en": "mlx-community/whisper-base.en-mlx",
            "small": "mlx-community/whisper-small-mlx",
            "small.en": "mlx-community/whisper-small.en-mlx",
            "medium": "mlx-community/whisper-medium-mlx",
            "medium.en": "mlx-community/whisper-medium.en-mlx",
            "large-v2": "mlx-community/whisper-large-v2-mlx",
            "large-v3": "mlx-community/whisper-large-v3-mlx",
            "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
        }
        mlx_model = model_map.get(model_name, f"mlx-community/whisper-{model_name}-mlx")

        result = mlx_whisper.transcribe(
            str(audio_path),
            path_or_hf_repo=mlx_model,
        )

        segments = [
            TranscriptSegment(
                start=seg["start"],
                end=seg["end"],
                text=seg["text"],
            )
            for seg in result.get("segments", [])
        ]

        return TranscriptResult(
            segments=segments,
            language=result.get("language", "unknown"),
            language_probability=1.0,  # mlx-whisper doesn't provide this
        )


def get_transcription_service() -> TranscriptionService:
    """Get the singleton transcription service instance."""
    return TranscriptionService()


def transcribe_audio(audio_path: str, include_timestamps: bool = True, title: str = "") -> str:
    """Transcribe an audio file and return the transcript as markdown.

    This is a convenience function for remote nodes that just need the transcript text.

    Args:
        audio_path: Path to the audio file.
        include_timestamps: Whether to include timestamps in output.
        title: Optional title for the transcript.

    Returns:
        Markdown formatted transcript text.
    """
    service = get_transcription_service()
    result = service.transcribe(Path(audio_path))
    return result.to_markdown(title=title, include_timestamps=include_timestamps)


def transcribe_episode(episode: Episode, feed: Feed, include_timestamps: bool = True) -> Path:
    """Transcribe an episode and save the result.

    Args:
        episode: Episode to transcribe (must have audio_path set).
        feed: Feed the episode belongs to.
        include_timestamps: Whether to include timestamps in output (default True).

    Returns:
        Path to the transcript file.

    Raises:
        ValueError: If episode has no audio path.
        Exception: If transcription fails.
    """
    if not episode.audio_path:
        raise ValueError(f"Episode {episode.id} has no audio path")

    audio_path = Path(episode.audio_path)
    if not audio_path.exists():
        raise ValueError(f"Audio file not found: {audio_path}")

    # Ensure directories exist
    _, transcripts_dir = ensure_podcast_directories(feed.title)

    # Get transcript path
    transcript_path = get_transcript_path(
        feed.title,
        episode.title,
        episode.published_at,
    )

    with get_db() as conn:
        repo = EpisodeRepository(conn)

        # Update status to transcribing
        repo.update_status(episode.id, EpisodeStatus.TRANSCRIBING)

        try:
            # Run transcription
            service = get_transcription_service()
            result = service.transcribe(audio_path)

            # Write transcript to file
            markdown = result.to_markdown(
                title=episode.title,
                include_timestamps=include_timestamps,
            )
            transcript_path.write_text(markdown, encoding="utf-8")

            # Update episode
            repo.update_transcript_path(episode.id, str(transcript_path))
            repo.update_status(episode.id, EpisodeStatus.COMPLETED)

            # Index transcript for full-text search (only if timestamps included)
            if include_timestamps:
                try:
                    from cast2md.search.repository import TranscriptSearchRepository
                    search_repo = TranscriptSearchRepository(conn)
                    search_repo.index_episode(episode.id, str(transcript_path))
                except Exception as index_error:
                    # Don't fail transcription if indexing fails
                    import logging
                    logging.getLogger(__name__).warning(
                        f"Failed to index transcript for episode {episode.id}: {index_error}"
                    )

            return transcript_path

        except Exception as e:
            repo.update_status(
                episode.id,
                EpisodeStatus.FAILED,
                error_message=str(e),
            )
            raise
