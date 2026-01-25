"""Audio preprocessing utilities.

Converts audio to mono 16kHz WAV format for optimal Whisper transcription.
Original files are preserved - preprocessing creates a temporary file.
"""

import logging
import subprocess
import uuid
from pathlib import Path

from cast2md.config.settings import get_settings

logger = logging.getLogger(__name__)


class PreprocessingError(Exception):
    """Error during audio preprocessing."""

    pass


def preprocess_audio(audio_path: Path) -> Path:
    """Preprocess audio file before transcription.

    Converts to mono 16kHz WAV format which is what Whisper expects internally.
    This reduces file size, speeds up processing, and avoids internal conversion.

    Args:
        audio_path: Path to the audio file.

    Returns:
        Path to the preprocessed audio file (temporary file in temp directory).

    Raises:
        PreprocessingError: If ffmpeg fails or is not available.
    """
    settings = get_settings()
    temp_dir = settings.temp_download_path
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Generate unique temp filename
    temp_filename = f"preprocess_{uuid.uuid4().hex[:8]}.wav"
    temp_path = temp_dir / temp_filename

    try:
        # Use ffmpeg to convert to mono 16kHz WAV
        cmd = [
            "ffmpeg",
            "-i", str(audio_path),
            "-ac", "1",           # mono
            "-ar", "16000",       # 16kHz sample rate
            "-acodec", "pcm_s16le",  # 16-bit PCM
            "-y",                 # overwrite if exists
            str(temp_path),
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout for long files
        )

        if result.returncode != 0:
            # Clean up temp file on failure
            if temp_path.exists():
                temp_path.unlink()
            raise PreprocessingError(
                f"ffmpeg failed with code {result.returncode}: {result.stderr}"
            )

        logger.info(
            f"Preprocessed audio: {audio_path.name} -> {temp_path.name} "
            f"({audio_path.stat().st_size / 1024 / 1024:.1f}MB -> "
            f"{temp_path.stat().st_size / 1024 / 1024:.1f}MB)"
        )

        return temp_path

    except FileNotFoundError:
        raise PreprocessingError(
            "ffmpeg not found. Please install ffmpeg to enable audio preprocessing."
        )
    except subprocess.TimeoutExpired:
        if temp_path.exists():
            temp_path.unlink()
        raise PreprocessingError("ffmpeg timed out during preprocessing")
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        raise PreprocessingError(f"Preprocessing failed: {e}")


def cleanup_preprocessed(temp_path: Path, original_path: Path) -> None:
    """Clean up temporary preprocessed file.

    Args:
        temp_path: Path to the preprocessed file.
        original_path: Path to the original file (for comparison).
    """
    # Only delete if it's actually a temp file (not the original)
    if temp_path != original_path and temp_path.exists():
        try:
            temp_path.unlink()
            logger.debug(f"Cleaned up preprocessed file: {temp_path}")
        except Exception as e:
            logger.warning(f"Failed to clean up preprocessed file {temp_path}: {e}")


def get_audio_duration(audio_path: Path) -> float:
    """Get audio duration in seconds using ffprobe.

    This is memory-efficient as it doesn't load the file into memory.

    Args:
        audio_path: Path to the audio file.

    Returns:
        Duration in seconds.

    Raises:
        PreprocessingError: If ffprobe fails.
    """
    try:
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(audio_path),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0 or not result.stdout.strip():
            raise PreprocessingError(
                f"ffprobe failed to get duration: {result.stderr}"
            )

        return float(result.stdout.strip())

    except FileNotFoundError:
        raise PreprocessingError(
            "ffprobe not found. Please install ffmpeg to enable audio preprocessing."
        )
    except subprocess.TimeoutExpired:
        raise PreprocessingError("ffprobe timed out getting audio duration")
    except ValueError as e:
        raise PreprocessingError(f"Failed to parse audio duration: {e}")


def extract_audio_chunk(
    audio_path: Path,
    start_sec: float,
    duration_sec: float,
    output_path: Path,
) -> Path:
    """Extract a chunk of audio using ffmpeg seeking.

    This is memory-efficient as it uses ffmpeg's input seeking
    to avoid loading the entire file.

    Args:
        audio_path: Path to the source audio file.
        start_sec: Start time in seconds.
        duration_sec: Duration to extract in seconds.
        output_path: Path for the output chunk file.

    Returns:
        Path to the extracted chunk.

    Raises:
        PreprocessingError: If ffmpeg fails.
    """
    try:
        cmd = [
            "ffmpeg",
            "-ss", str(start_sec),  # Input seeking (before -i for efficiency)
            "-i", str(audio_path),
            "-t", str(duration_sec),
            "-ac", "1",           # mono
            "-ar", "16000",       # 16kHz sample rate
            "-acodec", "pcm_s16le",  # 16-bit PCM
            "-y",                 # overwrite if exists
            str(output_path),
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout per chunk
        )

        if result.returncode != 0:
            # Clean up partial output on failure
            if output_path.exists():
                output_path.unlink()
            raise PreprocessingError(
                f"ffmpeg chunk extraction failed: {result.stderr}"
            )

        return output_path

    except FileNotFoundError:
        raise PreprocessingError(
            "ffmpeg not found. Please install ffmpeg to enable audio preprocessing."
        )
    except subprocess.TimeoutExpired:
        if output_path.exists():
            output_path.unlink()
        raise PreprocessingError("ffmpeg timed out during chunk extraction")
