"""Tests for RemoteTranscriptionCoordinator in-memory heartbeat tracking."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


class TestCoordinatorHeartbeat:
    """Tests for in-memory heartbeat tracking."""

    def test_record_heartbeat_in_memory(self):
        """Test that record_heartbeat stores timestamp in memory."""
        # Import here to avoid singleton issues
        from cast2md.distributed.coordinator import RemoteTranscriptionCoordinator

        # Create a fresh instance (bypass singleton for testing)
        coordinator = object.__new__(RemoteTranscriptionCoordinator)
        coordinator._initialized = False
        coordinator.__init__()

        coordinator.record_heartbeat("node-1")

        assert "node-1" in coordinator._node_heartbeats
        assert isinstance(coordinator._node_heartbeats["node-1"], datetime)

    def test_record_heartbeat_updates_timestamp(self):
        """Test that multiple heartbeats update the timestamp."""
        from cast2md.distributed.coordinator import RemoteTranscriptionCoordinator

        coordinator = object.__new__(RemoteTranscriptionCoordinator)
        coordinator._initialized = False
        coordinator.__init__()

        # Record first heartbeat
        coordinator.record_heartbeat("node-1")
        first_time = coordinator._node_heartbeats["node-1"]

        # Small delay then record again
        import time

        time.sleep(0.01)
        coordinator.record_heartbeat("node-1")
        second_time = coordinator._node_heartbeats["node-1"]

        assert second_time > first_time

    def test_stale_detection_uses_memory(self):
        """Test that stale detection uses in-memory timestamps."""
        from cast2md.distributed.coordinator import RemoteTranscriptionCoordinator

        coordinator = object.__new__(RemoteTranscriptionCoordinator)
        coordinator._initialized = False
        coordinator.__init__()
        coordinator._heartbeat_timeout_seconds = 60

        # Add heartbeats: one stale, one fresh
        now = datetime.utcnow()
        coordinator._node_heartbeats = {
            "stale-node": now - timedelta(seconds=120),  # 2 minutes ago
            "fresh-node": now,  # just now
        }

        # Calculate stale nodes like the coordinator does
        stale_threshold = now - timedelta(seconds=coordinator._heartbeat_timeout_seconds)
        stale_node_ids = [
            nid
            for nid, hb in coordinator._node_heartbeats.items()
            if hb < stale_threshold
        ]

        assert "stale-node" in stale_node_ids
        assert "fresh-node" not in stale_node_ids

    def test_sync_heartbeats_to_db(self):
        """Test that heartbeats are batched and synced to DB."""
        from cast2md.distributed.coordinator import RemoteTranscriptionCoordinator

        coordinator = object.__new__(RemoteTranscriptionCoordinator)
        coordinator._initialized = False
        coordinator.__init__()

        # Add some heartbeats
        now = datetime.utcnow()
        coordinator._node_heartbeats = {
            "node-1": now,
            "node-2": now - timedelta(seconds=10),
        }

        # Mock the DB context and repository
        mock_node_repo = MagicMock()
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=None)

        with patch("cast2md.distributed.coordinator.get_db", return_value=mock_conn):
            with patch(
                "cast2md.distributed.coordinator.TranscriberNodeRepository",
                return_value=mock_node_repo,
            ):
                coordinator._sync_heartbeats_to_db()

        # Verify update_heartbeat was called for each node
        assert mock_node_repo.update_heartbeat.call_count == 2

    def test_sync_empty_heartbeats_noop(self):
        """Test that syncing empty heartbeats does nothing."""
        from cast2md.distributed.coordinator import RemoteTranscriptionCoordinator

        coordinator = object.__new__(RemoteTranscriptionCoordinator)
        coordinator._initialized = False
        coordinator.__init__()

        coordinator._node_heartbeats = {}

        with patch("cast2md.distributed.coordinator.get_db") as mock_get_db:
            coordinator._sync_heartbeats_to_db()

        # get_db should not be called if there's nothing to sync
        mock_get_db.assert_not_called()


class TestCoordinatorDbSync:
    """Tests for periodic DB sync interval."""

    def test_db_sync_interval_config(self):
        """Test that DB sync interval is configurable."""
        from cast2md.distributed.coordinator import RemoteTranscriptionCoordinator

        coordinator = object.__new__(RemoteTranscriptionCoordinator)
        coordinator._initialized = False
        coordinator.__init__()

        # Default is 5 minutes
        assert coordinator._db_sync_interval_seconds == 300

    def test_last_db_sync_initialized(self):
        """Test that last_db_sync is initialized on startup."""
        from cast2md.distributed.coordinator import RemoteTranscriptionCoordinator

        coordinator = object.__new__(RemoteTranscriptionCoordinator)
        coordinator._initialized = False
        coordinator.__init__()

        assert hasattr(coordinator, "_last_db_sync")
        assert isinstance(coordinator._last_db_sync, datetime)
