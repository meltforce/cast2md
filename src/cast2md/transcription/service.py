"""Transcription service supporting faster-whisper and mlx-whisper backends."""

from __future__ import annotations

import logging
import platform
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from cast2md.config.settings import get_settings
from cast2md.transcription.preprocessing import cleanup_preprocessed, preprocess_audio

# Type hints only - these imports don't execute at runtime for node installs
if TYPE_CHECKING:
    from cast2md.db.models import Episode, Feed

logger = logging.getLogger(__name__)


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


def _get_transcription_backend() -> str:
    """Determine which transcription backend to use (whisper or parakeet)."""
    settings = get_settings()
    return settings.transcription_backend


def _get_whisper_backend() -> str:
    """Determine which Whisper backend to use (faster-whisper or mlx)."""
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
                    cls._instance._whisper_backend = None
                    cls._instance._transcription_backend = None
        return cls._instance

    @property
    def model(self):
        """Lazy-load the transcription model."""
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    self._load_model()
        return self._model

    @property
    def transcription_backend(self) -> str:
        """Get the transcription backend (whisper or parakeet)."""
        if self._transcription_backend is None:
            self._transcription_backend = _get_transcription_backend()
        return self._transcription_backend

    @property
    def whisper_backend(self) -> str:
        """Get the Whisper backend name (faster-whisper or mlx)."""
        if self._whisper_backend is None:
            self._whisper_backend = _get_whisper_backend()
        return self._whisper_backend

    def _load_model(self) -> None:
        """Load the transcription model based on settings."""
        settings = get_settings()

        if self.transcription_backend == "parakeet":
            # Parakeet loads per-call via NeMo, store config only
            logger.info("Using Parakeet TDT 0.6B v3 backend")
            self._model = {"backend": "parakeet", "model": "nvidia/parakeet-tdt-0.6b-v3"}
        elif self.whisper_backend == "mlx":
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

    def transcribe(
        self,
        audio_path: Path,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> TranscriptResult:
        """Transcribe an audio file.

        Args:
            audio_path: Path to the audio file.
            progress_callback: Optional callback that receives progress percentage (0-100).

        Returns:
            TranscriptResult with segments and metadata.
        """
        # Preprocess audio to mono 16kHz WAV (creates temp file)
        processed_path = preprocess_audio(audio_path)

        try:
            if self.transcription_backend == "parakeet":
                return self._transcribe_parakeet(processed_path)
            elif self.whisper_backend == "mlx":
                # mlx-whisper returns all segments at once, no streaming progress
                return self._transcribe_mlx(processed_path)
            else:
                return self._transcribe_faster_whisper(processed_path, progress_callback)
        finally:
            # Clean up preprocessed temp file (preserves original)
            cleanup_preprocessed(processed_path, audio_path)

    def _transcribe_faster_whisper(
        self,
        audio_path: Path,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> TranscriptResult:
        """Transcribe using faster-whisper backend."""
        segments_iter, info = self.model.transcribe(
            str(audio_path),
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
            ),
        )

        segments = []
        duration = info.duration if info.duration else 0

        for seg in segments_iter:
            segments.append(
                TranscriptSegment(
                    start=seg.start,
                    end=seg.end,
                    text=seg.text,
                )
            )
            # Report progress based on segment end time vs total duration
            if progress_callback and duration > 0:
                progress = int((seg.end / duration) * 100)
                progress = min(99, progress)  # Cap at 99 until complete
                progress_callback(progress)

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

    def _transcribe_parakeet(self, audio_path: Path) -> TranscriptResult:
        """Transcribe using NVIDIA Parakeet TDT 0.6B v3 backend.

        Uses NeMo toolkit with the nvidia/parakeet-tdt-0.6b-v3 model from HuggingFace.
        This is optimized for GPU transcription on RunPod workers.

        Long audio is processed in chunks to avoid GPU OOM errors.
        """
        import tempfile

        import nemo.collections.asr as nemo_asr
        from pydub import AudioSegment

        model_name = self.model["model"]
        logger.info(f"Loading Parakeet model: {model_name}")

        # Load the model from HuggingFace
        asr_model = nemo_asr.models.ASRModel.from_pretrained(model_name)

        # Check audio duration and chunk if needed
        # 10 minutes per chunk is safe for 24GB VRAM
        CHUNK_DURATION_MS = 10 * 60 * 1000  # 10 minutes in milliseconds

        audio = AudioSegment.from_file(str(audio_path))
        duration_ms = len(audio)
        logger.info(f"Audio duration: {duration_ms / 1000 / 60:.1f} minutes")

        all_segments = []

        if duration_ms <= CHUNK_DURATION_MS:
            # Short audio - process whole file
            all_segments = self._transcribe_parakeet_file(asr_model, audio_path)
        else:
            # Long audio - process in chunks
            num_chunks = (duration_ms + CHUNK_DURATION_MS - 1) // CHUNK_DURATION_MS
            logger.info(f"Splitting into {num_chunks} chunks for GPU memory")

            with tempfile.TemporaryDirectory() as tmpdir:
                for i in range(num_chunks):
                    start_ms = i * CHUNK_DURATION_MS
                    end_ms = min((i + 1) * CHUNK_DURATION_MS, duration_ms)
                    chunk = audio[start_ms:end_ms]

                    # Export chunk to temp file
                    chunk_path = Path(tmpdir) / f"chunk_{i}.wav"
                    chunk.export(str(chunk_path), format="wav")

                    logger.info(f"Transcribing chunk {i + 1}/{num_chunks}")
                    chunk_segments = self._transcribe_parakeet_file(asr_model, chunk_path)

                    # Adjust timestamps for chunk offset
                    offset_seconds = start_ms / 1000
                    for seg in chunk_segments:
                        seg.start += offset_seconds
                        seg.end += offset_seconds
                        all_segments.append(seg)

        return TranscriptResult(
            segments=all_segments,
            language="en",  # Parakeet supports 25 EU languages but defaults to English
            language_probability=1.0,
        )

    def _transcribe_parakeet_file(self, asr_model, audio_path: Path) -> list[TranscriptSegment]:
        """Transcribe a single audio file with Parakeet.

        Args:
            asr_model: Loaded NeMo ASR model
            audio_path: Path to audio file

        Returns:
            List of transcript segments
        """
        import torch

        # Clear GPU cache before transcription
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        # Transcribe with timestamps
        output = asr_model.transcribe(
            [str(audio_path)],
            timestamps=True,
        )

        # Parse the output - Parakeet returns a list of hypotheses
        segments = []
        if output and len(output) > 0:
            # output[0] is the transcription result
            result = output[0]

            # Handle different output formats from NeMo
            if hasattr(result, "timestamp") and result.timestamp:
                # Word-level timestamps available
                for ts in result.timestamp:
                    segments.append(
                        TranscriptSegment(
                            start=ts["start"],
                            end=ts["end"],
                            text=ts["word"],
                        )
                    )
            elif hasattr(result, "text"):
                # No timestamps, just text
                segments.append(
                    TranscriptSegment(
                        start=0.0,
                        end=0.0,
                        text=result.text,
                    )
                )
            elif isinstance(result, str):
                # Plain string result
                segments.append(
                    TranscriptSegment(
                        start=0.0,
                        end=0.0,
                        text=result,
                    )
                )

        return segments


def get_transcription_service() -> TranscriptionService:
    """Get the singleton transcription service instance."""
    return TranscriptionService()


def get_current_model_name() -> str:
    """Get the name of the currently configured transcription model.

    Returns the appropriate model name based on the active backend:
    - For Parakeet: "parakeet-tdt-0.6b-v3"
    - For Whisper: the configured whisper_model setting
    """
    settings = get_settings()
    if settings.transcription_backend == "parakeet":
        return "parakeet-tdt-0.6b-v3"
    return settings.whisper_model


def transcribe_audio(
    audio_path: str,
    include_timestamps: bool = True,
    title: str = "",
    progress_callback: Optional[Callable[[int], None]] = None,
) -> str:
    """Transcribe an audio file and return the transcript as markdown.

    This is a convenience function for remote nodes that just need the transcript text.

    Args:
        audio_path: Path to the audio file.
        include_timestamps: Whether to include timestamps in output.
        title: Optional title for the transcript.
        progress_callback: Optional callback that receives progress percentage (0-100).

    Returns:
        Markdown formatted transcript text.
    """
    service = get_transcription_service()
    result = service.transcribe(Path(audio_path), progress_callback=progress_callback)
    return result.to_markdown(title=title, include_timestamps=include_timestamps)


def transcribe_episode(
    episode: Episode,
    feed: Feed,
    include_timestamps: bool = True,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> Path:
    """Transcribe an episode and save the result.

    Args:
        episode: Episode to transcribe (must have audio_path set).
        feed: Feed the episode belongs to.
        include_timestamps: Whether to include timestamps in output (default True).
        progress_callback: Optional callback that receives progress percentage (0-100).

    Returns:
        Path to the transcript file.

    Raises:
        ValueError: If episode has no audio path.
        Exception: If transcription fails.
    """
    # Lazy imports for DB dependencies - keeps node installs lightweight
    from cast2md.db.connection import get_db
    from cast2md.db.models import EpisodeStatus
    from cast2md.db.repository import EpisodeRepository
    from cast2md.storage.filesystem import ensure_podcast_directories, get_transcript_path

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
            result = service.transcribe(audio_path, progress_callback=progress_callback)

            # Write transcript to file
            markdown = result.to_markdown(
                title=episode.title,
                include_timestamps=include_timestamps,
            )
            transcript_path.write_text(markdown, encoding="utf-8")

            # Update episode with transcript path and model name
            model_name = get_current_model_name()
            repo.update_transcript_path_and_model(
                episode.id, str(transcript_path), model_name
            )
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
