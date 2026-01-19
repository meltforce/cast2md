"""Audio preprocessing utilities.

This module is a passthrough stub for Phase 1.
Future phases may add audio normalization, silence removal, etc.
"""

from pathlib import Path


def preprocess_audio(audio_path: Path) -> Path:
    """Preprocess audio file before transcription.

    Currently a passthrough - returns the input path unchanged.
    Future versions may add:
    - Audio normalization
    - Silence trimming
    - Format conversion
    - Sample rate adjustment

    Args:
        audio_path: Path to the audio file.

    Returns:
        Path to the preprocessed audio file (same as input for now).
    """
    return audio_path
