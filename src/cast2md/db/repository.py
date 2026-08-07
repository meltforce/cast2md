"""Backwards-compatible import path for the repository classes.

The implementations live in cast2md.db.repositories, one module per
domain. This module keeps the `from cast2md.db.repository import X` path
that the rest of the codebase and the tests use.
"""

from cast2md.db.repositories import (
    EpisodeRepository,
    FeedRepository,
    JobRepository,
    PodRunRepository,
    PodSetupStateRepository,
    PodSetupStateRow,
    RunPodModel,
    RunPodModelRepository,
    SettingsRepository,
    TranscriberNodeRepository,
    WhisperModel,
    WhisperModelRepository,
)

__all__ = [
    "EpisodeRepository",
    "FeedRepository",
    "JobRepository",
    "PodRunRepository",
    "PodSetupStateRepository",
    "PodSetupStateRow",
    "RunPodModel",
    "RunPodModelRepository",
    "SettingsRepository",
    "TranscriberNodeRepository",
    "WhisperModel",
    "WhisperModelRepository",
]
