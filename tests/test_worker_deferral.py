"""Tests for when the server's transcribe worker defers to external workers.

With distributed transcription on, the server normally stays in standby while a
node or RunPod pod is available, so transcription does not compete with the web
app for server CPU. `server_transcription_always` opts out of that. The two
paths are hard to tell apart from the outside -- a server that defers and a
server with an empty queue both look idle -- so the behaviour is pinned here.
"""

import sys
import time
import types

import pytest

import cast2md.config.settings as cfg
from cast2md.worker.manager import WorkerManager

GRACE_SECONDS = 30


@pytest.fixture
def external_worker_present(monkeypatch):
    """Report one external worker without standing up a coordinator."""
    stub = types.ModuleType("cast2md.distributed.coordinator")
    stub.get_coordinator = lambda: types.SimpleNamespace(has_external_workers=lambda: True)
    monkeypatch.setitem(sys.modules, "cast2md.distributed.coordinator", stub)


@pytest.fixture
def worker(monkeypatch):
    """A WorkerManager whose settings and startup time the test controls."""

    def _configure(*, distributed: bool, always: bool, in_grace_period: bool):
        monkeypatch.setattr(
            cfg,
            "_settings",
            cfg.Settings(
                distributed_transcription_enabled=distributed,
                server_transcription_always=always,
            ),
        )
        manager = WorkerManager()
        manager._started_at = time.time() if in_grace_period else time.time() - 2 * GRACE_SECONDS
        return manager

    return _configure


def test_defers_while_an_external_worker_is_available(worker, external_worker_present):
    manager = worker(distributed=True, always=False, in_grace_period=False)

    assert manager._should_defer_transcription() is True


def test_defers_during_the_startup_grace_period(worker, external_worker_present):
    """The grace period keeps the server from claiming jobs before nodes announce."""
    manager = worker(distributed=True, always=False, in_grace_period=True)

    assert manager._should_defer_transcription() is True


def test_always_flag_claims_jobs_alongside_external_workers(worker, external_worker_present):
    manager = worker(distributed=True, always=True, in_grace_period=False)

    assert manager._should_defer_transcription() is False


def test_always_flag_skips_the_grace_period(worker, external_worker_present):
    """The grace period exists to let nodes go first, which this flag opts out of."""
    manager = worker(distributed=True, always=True, in_grace_period=True)

    assert manager._should_defer_transcription() is False


def test_never_defers_with_distributed_transcription_off(worker, external_worker_present):
    manager = worker(distributed=False, always=False, in_grace_period=False)

    assert manager._should_defer_transcription() is False
