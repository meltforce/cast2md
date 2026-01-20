"""Distributed transcription support."""

from cast2md.distributed.coordinator import (
    RemoteTranscriptionCoordinator,
    get_coordinator,
)

__all__ = ["RemoteTranscriptionCoordinator", "get_coordinator"]
