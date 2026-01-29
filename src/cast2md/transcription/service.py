"""Transcription service supporting faster-whisper and mlx-whisper backends."""

from __future__ import annotations

import logging
import platform
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from cast2md.config.settings import get_settings
from cast2md.transcription.preprocessing import (
    cleanup_preprocessed,
    extract_audio_chunk,
    get_audio_duration,
    preprocess_audio,
)

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
                    # Parakeet model caching (separate from Whisper)
                    cls._instance._parakeet_model = None
                    cls._instance._parakeet_model_name = None
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

    def _get_parakeet_model(self, model_name: str):
        """Get or load the Parakeet ASR model, caching for reuse.

        If the model name changed, releases the old model first to prevent OOM.
        """
        import torch

        # If model name changed, release old model first
        if self._parakeet_model is not None and self._parakeet_model_name != model_name:
            logger.info(f"Releasing old Parakeet model: {self._parakeet_model_name}")
            del self._parakeet_model
            torch.cuda.empty_cache()
            self._parakeet_model = None

        if self._parakeet_model is None:
            import nemo.collections.asr as nemo_asr

            logger.info(f"Loading Parakeet model: {model_name}")
            self._parakeet_model = nemo_asr.models.ASRModel.from_pretrained(model_name)
            self._parakeet_model_name = model_name

            # Disable CUDA graphs to avoid CUDA error 35 on RunPod.
            # NeMo 2.6+ ignores NEMO_CUDA_GRAPHS env var. The decoding config must
            # be patched so that new decoders (created by transcribe(timestamps=True))
            # are always created with CUDA graphs disabled.
            try:
                from omegaconf import OmegaConf, open_dict

                with open_dict(self._parakeet_model.cfg):
                    if hasattr(self._parakeet_model.cfg, "decoding"):
                        self._parakeet_model.cfg.decoding.greedy = (
                            self._parakeet_model.cfg.decoding.get("greedy", OmegaConf.create({}))
                        )
                        self._parakeet_model.cfg.decoding.greedy.use_cuda_graph_decoder = False
                        logger.info("Disabled CUDA graphs in Parakeet model config")
            except Exception as e:
                logger.warning(f"Could not disable CUDA graphs in config: {e}")

        return self._parakeet_model

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
        """Transcribe using faster-whisper backend.

        For long audio files (> chunk threshold), uses chunked processing
        to avoid loading the entire file into memory.
        """
        settings = get_settings()
        threshold_seconds = settings.whisper_chunk_threshold_minutes * 60

        # Check duration without loading file into memory
        try:
            duration = get_audio_duration(audio_path)
        except Exception as e:
            logger.warning(f"Could not get audio duration, using non-chunked mode: {e}")
            duration = 0

        # Use chunked processing for long files
        if duration > threshold_seconds:
            logger.info(
                f"Audio duration {duration / 60:.1f} min exceeds threshold "
                f"({settings.whisper_chunk_threshold_minutes} min), using chunked processing"
            )
            return self._transcribe_faster_whisper_chunked(
                audio_path, duration, progress_callback
            )

        # Short audio - process whole file
        return self._transcribe_faster_whisper_single(audio_path, progress_callback)

    def _transcribe_faster_whisper_single(
        self,
        audio_path: Path,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> TranscriptResult:
        """Transcribe a single audio file using faster-whisper (non-chunked)."""
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

    def _transcribe_faster_whisper_chunked(
        self,
        audio_path: Path,
        total_duration: float,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> TranscriptResult:
        """Transcribe long audio in chunks for memory efficiency.

        Uses ffmpeg to extract chunks without loading entire file,
        then combines results with adjusted timestamps.
        """
        settings = get_settings()
        chunk_size_seconds = settings.whisper_chunk_size_minutes * 60
        temp_dir = settings.temp_download_path
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Calculate number of chunks
        num_chunks = int((total_duration + chunk_size_seconds - 1) // chunk_size_seconds)
        logger.info(
            f"Splitting {total_duration / 60:.1f} min audio into {num_chunks} chunks "
            f"of {settings.whisper_chunk_size_minutes} min each"
        )

        all_segments = []
        detected_language = None
        language_probability = 0.0
        chunk_paths = []

        try:
            for i in range(num_chunks):
                start_sec = i * chunk_size_seconds
                # For last chunk, use remaining duration
                this_chunk_duration = min(chunk_size_seconds, total_duration - start_sec)

                # Generate unique chunk filename
                chunk_filename = f"chunk_{uuid.uuid4().hex[:8]}_{i}.wav"
                chunk_path = temp_dir / chunk_filename
                chunk_paths.append(chunk_path)

                # Extract chunk using ffmpeg (memory efficient)
                logger.info(
                    f"Extracting chunk {i + 1}/{num_chunks}: "
                    f"{start_sec / 60:.1f}-{(start_sec + this_chunk_duration) / 60:.1f} min"
                )
                extract_audio_chunk(audio_path, start_sec, this_chunk_duration, chunk_path)

                # Transcribe chunk
                logger.info(f"Transcribing chunk {i + 1}/{num_chunks}")
                segments_iter, info = self.model.transcribe(
                    str(chunk_path),
                    vad_filter=True,
                    vad_parameters=dict(
                        min_silence_duration_ms=500,
                    ),
                )

                # Capture language from first chunk
                if detected_language is None:
                    detected_language = info.language
                    language_probability = info.language_probability

                # Process segments with timestamp offset
                for seg in segments_iter:
                    all_segments.append(
                        TranscriptSegment(
                            start=seg.start + start_sec,  # Adjust for chunk offset
                            end=seg.end + start_sec,
                            text=seg.text,
                        )
                    )

                # Report progress based on chunks completed
                if progress_callback:
                    progress = int(((i + 1) / num_chunks) * 99)
                    progress_callback(progress)

                # Clean up chunk after processing
                if chunk_path.exists():
                    chunk_path.unlink()
                    chunk_paths.remove(chunk_path)

        finally:
            # Clean up any remaining chunk files on error
            for chunk_path in chunk_paths:
                if chunk_path.exists():
                    try:
                        chunk_path.unlink()
                    except Exception:
                        pass

        return TranscriptResult(
            segments=all_segments,
            language=detected_language or "unknown",
            language_probability=language_probability,
        )

    def _transcribe_mlx(self, audio_path: Path) -> TranscriptResult:
        """Transcribe using mlx-whisper backend.

        For long audio files (> chunk threshold), uses chunked processing
        to avoid memory issues on limited RAM systems (e.g., 8GB M1).
        """
        settings = get_settings()
        threshold_seconds = settings.whisper_chunk_threshold_minutes * 60

        # Check duration without loading file into memory
        try:
            duration = get_audio_duration(audio_path)
        except Exception as e:
            logger.warning(f"Could not get audio duration, using non-chunked mode: {e}")
            duration = 0

        # Use chunked processing for long files
        if duration > threshold_seconds:
            logger.info(
                f"Audio duration {duration / 60:.1f} min exceeds threshold "
                f"({settings.whisper_chunk_threshold_minutes} min), using chunked processing"
            )
            return self._transcribe_mlx_chunked(audio_path, duration)

        # Short audio - process whole file
        return self._transcribe_mlx_single(audio_path)

    def _transcribe_mlx_single(self, audio_path: Path) -> TranscriptResult:
        """Transcribe a single audio file using mlx-whisper (non-chunked)."""
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

    def _transcribe_mlx_chunked(
        self,
        audio_path: Path,
        total_duration: float,
    ) -> TranscriptResult:
        """Transcribe long audio in chunks using mlx-whisper.

        Uses ffmpeg to extract chunks without loading entire file,
        then combines results with adjusted timestamps.
        """
        import mlx_whisper

        settings = get_settings()
        chunk_size_seconds = settings.whisper_chunk_size_minutes * 60
        temp_dir = settings.temp_download_path
        temp_dir.mkdir(parents=True, exist_ok=True)

        model_name = self.model["model"]
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

        # Calculate number of chunks
        num_chunks = int((total_duration + chunk_size_seconds - 1) // chunk_size_seconds)
        logger.info(
            f"Splitting {total_duration / 60:.1f} min audio into {num_chunks} chunks "
            f"of {settings.whisper_chunk_size_minutes} min each"
        )

        all_segments = []
        detected_language = None
        chunk_paths = []

        try:
            for i in range(num_chunks):
                start_sec = i * chunk_size_seconds
                # For last chunk, use remaining duration
                this_chunk_duration = min(chunk_size_seconds, total_duration - start_sec)

                # Generate unique chunk filename
                chunk_filename = f"chunk_{uuid.uuid4().hex[:8]}_{i}.wav"
                chunk_path = temp_dir / chunk_filename
                chunk_paths.append(chunk_path)

                # Extract chunk using ffmpeg (memory efficient)
                logger.info(
                    f"Extracting chunk {i + 1}/{num_chunks}: "
                    f"{start_sec / 60:.1f}-{(start_sec + this_chunk_duration) / 60:.1f} min"
                )
                extract_audio_chunk(audio_path, start_sec, this_chunk_duration, chunk_path)

                # Transcribe chunk
                logger.info(f"Transcribing chunk {i + 1}/{num_chunks}")
                result = mlx_whisper.transcribe(
                    str(chunk_path),
                    path_or_hf_repo=mlx_model,
                )

                # Capture language from first chunk
                if detected_language is None:
                    detected_language = result.get("language", "unknown")

                # Process segments with timestamp offset
                for seg in result.get("segments", []):
                    all_segments.append(
                        TranscriptSegment(
                            start=seg["start"] + start_sec,  # Adjust for chunk offset
                            end=seg["end"] + start_sec,
                            text=seg["text"],
                        )
                    )

                # Clean up chunk after processing
                if chunk_path.exists():
                    chunk_path.unlink()
                    chunk_paths.remove(chunk_path)

        finally:
            # Clean up any remaining chunk files on error
            for chunk_path in chunk_paths:
                if chunk_path.exists():
                    try:
                        chunk_path.unlink()
                    except Exception:
                        pass

        return TranscriptResult(
            segments=all_segments,
            language=detected_language or "unknown",
            language_probability=1.0,
        )

    def _transcribe_parakeet(self, audio_path: Path) -> TranscriptResult:
        """Transcribe using NVIDIA Parakeet TDT 0.6B v3 backend.

        Uses NeMo toolkit with the nvidia/parakeet-tdt-0.6b-v3 model from HuggingFace.
        This is optimized for GPU transcription on RunPod workers.

        Optimization: Caches the Parakeet model between transcriptions (Phase 1).
        This saves ~30-40 seconds per episode by not reloading the model.

        Long audio is split into chunks to avoid GPU OOM errors. Chunks are
        processed sequentially (batch processing caused OOM due to upfront loading).
        """
        import tempfile

        import torch
        from pydub import AudioSegment

        model_name = self.model["model"]

        # Use cached model (Phase 1 optimization)
        asr_model = self._get_parakeet_model(model_name)

        # Check audio duration and chunk if needed
        # 10 minutes per chunk is safe for 24GB VRAM
        CHUNK_DURATION_MS = 10 * 60 * 1000  # 10 minutes in milliseconds

        audio = AudioSegment.from_file(str(audio_path))
        duration_ms = len(audio)
        logger.info(f"Audio duration: {duration_ms / 1000 / 60:.1f} minutes")

        # Clear GPU cache before transcription
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        if duration_ms <= CHUNK_DURATION_MS:
            # Short audio - process whole file directly
            all_segments = self._transcribe_parakeet_file(asr_model, audio_path)
        else:
            # Long audio - process chunks sequentially with cached model
            # Model caching (Phase 1) saves ~30-40s per episode
            # Sequential processing avoids OOM (batch loading uses too much VRAM)
            num_chunks = (duration_ms + CHUNK_DURATION_MS - 1) // CHUNK_DURATION_MS
            logger.info(f"Splitting into {num_chunks} chunks for sequential transcription")

            all_segments = []
            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir_path = Path(tmpdir)

                for i in range(num_chunks):
                    start_ms = i * CHUNK_DURATION_MS
                    end_ms = min((i + 1) * CHUNK_DURATION_MS, duration_ms)
                    chunk = audio[start_ms:end_ms]
                    offset_sec = start_ms / 1000

                    chunk_path = tmpdir_path / f"chunk_{i}.wav"
                    chunk.export(str(chunk_path), format="wav")

                    logger.info(f"Transcribing chunk {i + 1}/{num_chunks}")

                    # Clear GPU cache before each chunk to prevent fragmentation
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()

                    # Transcribe single chunk
                    chunk_segments = self._transcribe_parakeet_file(asr_model, chunk_path)

                    # Adjust timestamps by chunk offset
                    for seg in chunk_segments:
                        seg.start += offset_sec
                        seg.end += offset_sec
                    all_segments.extend(chunk_segments)

                    # Clean up chunk file immediately to save disk space
                    chunk_path.unlink()

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

        # Parse the output - NeMo returns a list of Hypothesis objects
        # Structure: output[0] is a list, each item has .text and .timestep dict
        segments = []
        if output and len(output) > 0:
            # Debug: log the actual structure
            logger.debug(f"NeMo output type: {type(output)}, len: {len(output)}")
            logger.debug(f"output[0] type: {type(output[0])}")
            if hasattr(output[0], "__dict__"):
                logger.debug(f"output[0] attrs: {list(output[0].__dict__.keys())}")

            # output[0] is a list of Hypothesis objects (one per audio file)
            hypotheses = output[0] if isinstance(output[0], list) else [output[0]]

            for hyp in hypotheses:
                # Handle different output formats from NeMo
                if hasattr(hyp, "timestamp") and hyp.timestamp:
                    # Word-level timestamps: hyp.timestamp['word'] is a list of (word, start, end)
                    word_timestamps = hyp.timestamp.get("word", [])
                    for ts in word_timestamps:
                        # ts is a tuple/list: (word, start_time, end_time)
                        if isinstance(ts, (list, tuple)) and len(ts) >= 3:
                            segments.append(
                                TranscriptSegment(
                                    start=float(ts[1]),
                                    end=float(ts[2]),
                                    text=str(ts[0]),
                                )
                            )
                        elif isinstance(ts, dict):
                            # Alternative dict format
                            segments.append(
                                TranscriptSegment(
                                    start=ts.get("start", 0.0),
                                    end=ts.get("end", 0.0),
                                    text=ts.get("word", ""),
                                )
                            )
                elif hasattr(hyp, "text") and hyp.text:
                    # No timestamps, just text
                    segments.append(
                        TranscriptSegment(
                            start=0.0,
                            end=0.0,
                            text=hyp.text,
                        )
                    )
                elif isinstance(hyp, str):
                    # Plain string result
                    segments.append(
                        TranscriptSegment(
                            start=0.0,
                            end=0.0,
                            text=hyp,
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
