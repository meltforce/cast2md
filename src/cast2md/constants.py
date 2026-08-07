"""Constants shared across packages.

This module imports nothing from cast2md, so any package may pull from it
without creating an import cycle.
"""

# Available transcription models for RunPod GPU workers
# Parakeet supports 25 European languages, Whisper supports 99+ languages
RUNPOD_TRANSCRIPTION_MODELS = [
    ("parakeet-tdt-0.6b-v3", "Parakeet TDT 0.6B v3 (fast, 25 EU languages)"),
    ("large-v3-turbo", "Whisper large-v3-turbo"),
    ("large-v3", "Whisper large-v3"),
    ("large-v2", "Whisper large-v2"),
    ("medium", "Whisper medium"),
    ("small", "Whisper small"),
]
