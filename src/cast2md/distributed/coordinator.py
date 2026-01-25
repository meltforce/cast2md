"""Remote transcription coordinator for managing distributed nodes."""

import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

from cast2md.db.connection import get_db
from cast2md.db.models import NodeStatus
from cast2md.db.repository import JobRepository, TranscriberNodeRepository

logger = logging.getLogger(__name__)


class RemoteTranscriptionCoordinator:
    """Coordinates remote transcription nodes.

    Responsibilities:
    - Monitor node heartbeats and mark offline after timeout
    - Reclaim stuck jobs from offline/unresponsive nodes
    - Track node status for the UI
    """

    _instance: Optional["RemoteTranscriptionCoordinator"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "RemoteTranscriptionCoordinator":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Configuration (can be overridden via settings)
        self._heartbeat_timeout_seconds = 60
        self._job_timeout_minutes = 30
        self._check_interval_seconds = 30

        # In-memory heartbeat tracking to reduce DB writes
        self._node_heartbeats: dict[str, datetime] = {}
        self._heartbeat_lock = threading.Lock()
        self._last_db_sync: datetime = datetime.now()
        self._db_sync_interval_seconds = 300  # Sync to DB every 5 minutes

    def start(self):
        """Start the coordinator background thread."""
        if self._running:
            logger.warning("Coordinator already running")
            return

        # Cleanup stale nodes on startup
        self._cleanup_stale_nodes()

        self._running = True
        self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._run,
            name="transcription-coordinator",
            daemon=True,
        )
        self._thread.start()
        logger.info("Started remote transcription coordinator")

    def _cleanup_stale_nodes(self, offline_hours: int = 24):
        """Clean up nodes that have been offline too long."""
        try:
            with get_db() as conn:
                node_repo = TranscriberNodeRepository(conn)
                deleted = node_repo.cleanup_stale_nodes(offline_hours)
                if deleted > 0:
                    logger.info(f"Cleaned up {deleted} stale nodes (offline > {offline_hours}h)")
        except Exception as e:
            logger.warning(f"Failed to cleanup stale nodes: {e}")

    def stop(self, timeout: float = 10.0):
        """Stop the coordinator."""
        if not self._running:
            return

        logger.info("Stopping coordinator...")
        self._stop_event.set()
        self._running = False

        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

        logger.info("Coordinator stopped")

    def _run(self):
        """Main coordinator loop."""
        while not self._stop_event.is_set():
            try:
                self._check_nodes()
                self._reclaim_stale_jobs()
            except Exception as e:
                logger.error(f"Coordinator error: {e}")

            # Wait for next check interval
            self._stop_event.wait(timeout=self._check_interval_seconds)

    def record_heartbeat(self, node_id: str) -> None:
        """Record heartbeat in memory (no DB write).

        This reduces DB lock contention by caching heartbeats in memory.
        Heartbeats are periodically synced to the database.
        """
        with self._heartbeat_lock:
            self._node_heartbeats[node_id] = datetime.now()

    def _sync_heartbeats_to_db(self) -> None:
        """Batch sync all heartbeat timestamps to DB."""
        with self._heartbeat_lock:
            heartbeats_copy = dict(self._node_heartbeats)

        if not heartbeats_copy:
            return

        with get_db() as conn:
            node_repo = TranscriberNodeRepository(conn)
            for node_id, hb_time in heartbeats_copy.items():
                try:
                    node_repo.update_heartbeat(node_id, hb_time)
                except Exception as e:
                    logger.debug(f"Failed to sync heartbeat for node {node_id}: {e}")

    def _check_nodes(self):
        """Check node heartbeats and mark stale nodes as offline."""
        now = datetime.now()

        # Periodic DB sync
        if (now - self._last_db_sync).total_seconds() >= self._db_sync_interval_seconds:
            self._sync_heartbeats_to_db()
            self._last_db_sync = now

        # Check stale using in-memory data
        stale_threshold = now - timedelta(seconds=self._heartbeat_timeout_seconds)

        with self._heartbeat_lock:
            stale_node_ids = [
                nid for nid, hb in self._node_heartbeats.items()
                if hb < stale_threshold
            ]

        # Mark stale nodes as offline
        if stale_node_ids:
            with get_db() as conn:
                node_repo = TranscriberNodeRepository(conn)
                for node_id in stale_node_ids:
                    node = node_repo.get_by_id(node_id)
                    if node and node.status != NodeStatus.OFFLINE:
                        logger.warning(f"Node '{node.name}' ({node.id}) is stale, marking offline")
                        node_repo.mark_offline(node_id)
                    with self._heartbeat_lock:
                        self._node_heartbeats.pop(node_id, None)

        # Also check DB for nodes not in memory (e.g., registered before coordinator started)
        with get_db() as conn:
            node_repo = TranscriberNodeRepository(conn)
            stale_nodes = node_repo.get_stale_nodes(
                timeout_seconds=self._heartbeat_timeout_seconds
            )
            for node in stale_nodes:
                # Skip if already processed or if we have a fresh in-memory heartbeat
                with self._heartbeat_lock:
                    has_fresh_heartbeat = (
                        node.id in self._node_heartbeats
                        and self._node_heartbeats[node.id] >= stale_threshold
                    )
                if node.id not in stale_node_ids and not has_fresh_heartbeat:
                    logger.warning(f"Node '{node.name}' ({node.id}) is stale, marking offline")
                    node_repo.mark_offline(node.id)

    def _reclaim_stale_jobs(self):
        """Reclaim jobs that have been running too long on nodes."""
        with get_db() as conn:
            job_repo = JobRepository(conn)

            # Reclaim jobs that have been running > timeout on any node
            requeued, failed = job_repo.reclaim_stale_jobs(
                timeout_minutes=self._job_timeout_minutes
            )

            if requeued > 0 or failed > 0:
                logger.info(
                    f"Reclaimed stale jobs: {requeued} requeued, {failed} failed (max attempts)"
                )

    def configure(
        self,
        heartbeat_timeout_seconds: int | None = None,
        job_timeout_minutes: int | None = None,
        check_interval_seconds: int | None = None,
    ):
        """Update coordinator configuration.

        Args:
            heartbeat_timeout_seconds: Seconds after which a node is considered stale.
            job_timeout_minutes: Minutes after which a running job is reclaimed.
            check_interval_seconds: How often to check for stale nodes/jobs.
        """
        if heartbeat_timeout_seconds is not None:
            self._heartbeat_timeout_seconds = heartbeat_timeout_seconds
        if job_timeout_minutes is not None:
            self._job_timeout_minutes = job_timeout_minutes
        if check_interval_seconds is not None:
            self._check_interval_seconds = check_interval_seconds

    @property
    def is_running(self) -> bool:
        """Check if coordinator is running."""
        return self._running

    def has_external_workers(self) -> bool:
        """Check if any external transcription workers are available.

        This includes:
        - Online/busy nodes (checked via in-memory heartbeats first, then DB)
        - Active RunPod pods (if RunPod is enabled)

        Used by local transcription worker to defer to external workers.
        """
        # Check in-memory heartbeats first (fast path)
        now = datetime.now()
        stale_threshold = now - timedelta(seconds=self._heartbeat_timeout_seconds)

        with self._heartbeat_lock:
            fresh_heartbeats = sum(
                1 for hb in self._node_heartbeats.values()
                if hb >= stale_threshold
            )
            if fresh_heartbeats > 0:
                return True

        # Check DB for online/busy nodes (in case of recently started nodes)
        try:
            with get_db() as conn:
                node_repo = TranscriberNodeRepository(conn)
                status_counts = node_repo.count_by_status()
                online_count = status_counts.get(NodeStatus.ONLINE.value, 0)
                busy_count = status_counts.get(NodeStatus.BUSY.value, 0)
                if online_count + busy_count > 0:
                    return True
        except Exception as e:
            logger.debug(f"Error checking node status: {e}")

        # Check for active RunPod pods
        try:
            from cast2md.services.runpod_service import get_runpod_service
            runpod_service = get_runpod_service()
            if runpod_service.is_available():
                pods = runpod_service.list_pods()
                if len(pods) > 0:
                    return True
        except Exception as e:
            logger.debug(f"Error checking RunPod pods: {e}")

        return False

    def get_status(self) -> dict:
        """Get coordinator status information."""
        with get_db() as conn:
            node_repo = TranscriberNodeRepository(conn)
            nodes = node_repo.get_all()
            status_counts = node_repo.count_by_status()

        online_count = status_counts.get(NodeStatus.ONLINE.value, 0)
        busy_count = status_counts.get(NodeStatus.BUSY.value, 0)
        offline_count = status_counts.get(NodeStatus.OFFLINE.value, 0)

        return {
            "running": self._running,
            "total_nodes": len(nodes),
            "online_nodes": online_count,
            "busy_nodes": busy_count,
            "offline_nodes": offline_count,
            "heartbeat_timeout_seconds": self._heartbeat_timeout_seconds,
            "job_timeout_minutes": self._job_timeout_minutes,
            "nodes": [
                {
                    "id": n.id,
                    "name": n.name,
                    "status": n.status.value,
                    "current_job_id": n.current_job_id,
                    "last_heartbeat": n.last_heartbeat.isoformat() if n.last_heartbeat else None,
                }
                for n in nodes
            ],
        }


# Global instance
_coordinator: Optional[RemoteTranscriptionCoordinator] = None


def get_coordinator() -> RemoteTranscriptionCoordinator:
    """Get or create the global coordinator instance."""
    global _coordinator
    if _coordinator is None:
        _coordinator = RemoteTranscriptionCoordinator()
    return _coordinator
