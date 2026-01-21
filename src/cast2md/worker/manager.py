"""Worker manager for coordinating download and transcription workers."""

import logging
import threading
import time
from typing import Optional

from cast2md.config.settings import get_settings
from cast2md.db.connection import get_db
from cast2md.db.models import EpisodeStatus, JobStatus, JobType
from cast2md.db.repository import EpisodeRepository, FeedRepository, JobRepository
from cast2md.download.downloader import download_episode
from cast2md.notifications.ntfy import (
    notify_download_failed,
    notify_transcription_complete,
    notify_transcription_failed,
)
from cast2md.transcription.service import transcribe_episode

logger = logging.getLogger(__name__)


def _is_distributed_enabled() -> bool:
    """Check if distributed transcription is enabled."""
    settings = get_settings()
    return settings.distributed_transcription_enabled


class WorkerManager:
    """Manages download and transcription workers."""

    _instance: Optional["WorkerManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "WorkerManager":
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
        self._download_threads: list[threading.Thread] = []
        self._transcribe_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._coordinator = None

        settings = get_settings()
        self._max_download_workers = settings.max_concurrent_downloads

    def start(self):
        """Start the worker threads."""
        if self._running:
            logger.warning("Workers already running")
            return

        self._running = True
        self._stop_event.clear()

        # Start download workers
        for i in range(self._max_download_workers):
            thread = threading.Thread(
                target=self._download_worker,
                name=f"download-worker-{i}",
                daemon=True,
            )
            thread.start()
            self._download_threads.append(thread)
            logger.info(f"Started download worker {i}")

        # Start transcription worker (single, sequential)
        self._transcribe_thread = threading.Thread(
            target=self._transcribe_worker,
            name="transcribe-worker",
            daemon=True,
        )
        self._transcribe_thread.start()
        logger.info("Started transcription worker")

        # Start distributed transcription coordinator if enabled
        if _is_distributed_enabled():
            from cast2md.distributed import get_coordinator

            settings = get_settings()
            self._coordinator = get_coordinator()
            self._coordinator.configure(
                heartbeat_timeout_seconds=settings.node_heartbeat_timeout_seconds,
                job_timeout_hours=settings.remote_job_timeout_hours,
            )
            self._coordinator.start()
            logger.info("Started distributed transcription coordinator")

    def stop(self, timeout: float = 30.0):
        """Stop all workers gracefully."""
        if not self._running:
            return

        logger.info("Stopping workers...")
        self._stop_event.set()
        self._running = False

        # Stop coordinator if running
        if self._coordinator:
            self._coordinator.stop()
            self._coordinator = None
            logger.info("Stopped distributed transcription coordinator")

        # Wait for download workers
        for thread in self._download_threads:
            thread.join(timeout=timeout / (self._max_download_workers + 1))

        # Wait for transcription worker
        if self._transcribe_thread:
            self._transcribe_thread.join(timeout=timeout / (self._max_download_workers + 1))

        self._download_threads.clear()
        self._transcribe_thread = None
        logger.info("All workers stopped")

    def _download_worker(self):
        """Worker thread for processing download jobs."""
        while not self._stop_event.is_set():
            try:
                job = self._get_next_job(JobType.DOWNLOAD)
                if job is None:
                    # No jobs, wait before checking again
                    self._stop_event.wait(timeout=5.0)
                    continue

                self._process_download_job(job.id, job.episode_id)

            except Exception as e:
                logger.error(f"Download worker error: {e}")
                time.sleep(5.0)

    def _transcribe_worker(self):
        """Worker thread for processing transcription jobs (sequential)."""
        while not self._stop_event.is_set():
            try:
                job = self._get_next_job(JobType.TRANSCRIBE)
                if job is None:
                    # No jobs, wait before checking again
                    self._stop_event.wait(timeout=5.0)
                    continue

                self._process_transcribe_job(job.id, job.episode_id)

            except Exception as e:
                logger.error(f"Transcription worker error: {e}")
                time.sleep(5.0)

    def _get_next_job(self, job_type: JobType):
        """Get the next available job from the queue.

        For transcription jobs when distributed transcription is enabled,
        only returns jobs not assigned to remote nodes.
        """
        with get_db() as conn:
            repo = JobRepository(conn)
            # For transcription jobs with distributed enabled, only get unassigned jobs
            local_only = (
                job_type == JobType.TRANSCRIBE and _is_distributed_enabled()
            )
            return repo.get_next_job(job_type, local_only=local_only)

    def _process_download_job(self, job_id: int, episode_id: int):
        """Process a download job."""
        logger.info(f"Processing download job {job_id} for episode {episode_id}")

        with get_db() as conn:
            job_repo = JobRepository(conn)
            episode_repo = EpisodeRepository(conn)
            feed_repo = FeedRepository(conn)

            # Mark job as running
            job_repo.mark_running(job_id)

            episode = episode_repo.get_by_id(episode_id)
            if not episode:
                job_repo.mark_failed(job_id, "Episode not found", retry=False)
                return

            feed = feed_repo.get_by_id(episode.feed_id)
            if not feed:
                job_repo.mark_failed(job_id, "Feed not found", retry=False)
                return

        try:
            # Perform the download (uses its own db connection)
            download_episode(episode, feed)

            with get_db() as conn:
                job_repo = JobRepository(conn)
                job_repo.mark_completed(job_id)
                logger.info(f"Download job {job_id} completed")

                # Auto-queue transcription job
                self._queue_transcription(conn, episode_id)

        except Exception as e:
            logger.error(f"Download job {job_id} failed: {e}")
            with get_db() as conn:
                job_repo = JobRepository(conn)
                job_repo.mark_failed(job_id, str(e))

            # Send failure notification
            notify_download_failed(episode.title, feed.title, str(e))

    def _process_transcribe_job(self, job_id: int, episode_id: int):
        """Process a transcription job."""
        logger.info(f"Processing transcription job {job_id} for episode {episode_id}")

        with get_db() as conn:
            job_repo = JobRepository(conn)
            episode_repo = EpisodeRepository(conn)
            feed_repo = FeedRepository(conn)

            # Mark job as running
            job_repo.mark_running(job_id)

            episode = episode_repo.get_by_id(episode_id)
            if not episode:
                job_repo.mark_failed(job_id, "Episode not found", retry=False)
                return

            feed = feed_repo.get_by_id(episode.feed_id)
            if not feed:
                job_repo.mark_failed(job_id, "Feed not found", retry=False)
                return

            if not episode.audio_path:
                job_repo.mark_failed(job_id, "Episode not downloaded", retry=False)
                return

        # Create progress callback that updates the database
        # Use time-based throttling (every 5 seconds) to reduce DB lock contention
        last_progress = [0]  # Use list to allow mutation in closure
        last_update_time = [time.time()]

        def progress_callback(progress: int):
            now = time.time()
            time_elapsed = (now - last_update_time[0]) >= 5.0
            is_completion = progress >= 99 and progress > last_progress[0]

            # Update every 5 seconds or at completion
            if (time_elapsed or is_completion) and progress > last_progress[0]:
                last_progress[0] = progress
                last_update_time[0] = now
                try:
                    with get_db() as conn:
                        job_repo = JobRepository(conn)
                        job_repo.update_progress(job_id, progress)
                except Exception as e:
                    logger.debug(f"Failed to update progress for job {job_id}: {e}")

        try:
            # Perform the transcription (uses its own db connection)
            transcribe_episode(episode, feed, progress_callback=progress_callback)

            with get_db() as conn:
                job_repo = JobRepository(conn)
                job_repo.mark_completed(job_id)
                logger.info(f"Transcription job {job_id} completed")

            # Send success notification
            notify_transcription_complete(episode.title, feed.title)

        except Exception as e:
            logger.error(f"Transcription job {job_id} failed: {e}")
            with get_db() as conn:
                job_repo = JobRepository(conn)
                job_repo.mark_failed(job_id, str(e))

            # Send failure notification
            notify_transcription_failed(episode.title, feed.title, str(e))

    def _queue_transcription(self, conn, episode_id: int):
        """Queue a transcription job for an episode."""
        job_repo = JobRepository(conn)

        # Check if already queued
        if job_repo.has_pending_job(episode_id, JobType.TRANSCRIBE):
            return

        job_repo.create(
            episode_id=episode_id,
            job_type=JobType.TRANSCRIBE,
            priority=1,  # High priority for newly downloaded
        )
        logger.info(f"Queued transcription for episode {episode_id}")

    @property
    def is_running(self) -> bool:
        """Check if workers are running."""
        return self._running

    def get_status(self) -> dict:
        """Get worker status."""
        with get_db() as conn:
            job_repo = JobRepository(conn)

            download_counts = job_repo.count_by_status(JobType.DOWNLOAD)
            transcribe_counts = job_repo.count_by_status(JobType.TRANSCRIBE)

            download_running = job_repo.get_running_jobs(JobType.DOWNLOAD)
            transcribe_running = job_repo.get_running_jobs(JobType.TRANSCRIBE)

        status = {
            "running": self._running,
            "download_workers": len(self._download_threads),
            "transcribe_workers": 1 if self._transcribe_thread else 0,
            "download_queue": {
                "queued": download_counts.get(JobStatus.QUEUED.value, 0),
                "running": len(download_running),
                "completed": download_counts.get(JobStatus.COMPLETED.value, 0),
                "failed": download_counts.get(JobStatus.FAILED.value, 0),
            },
            "transcribe_queue": {
                "queued": transcribe_counts.get(JobStatus.QUEUED.value, 0),
                "running": len(transcribe_running),
                "completed": transcribe_counts.get(JobStatus.COMPLETED.value, 0),
                "failed": transcribe_counts.get(JobStatus.FAILED.value, 0),
            },
            "distributed_enabled": _is_distributed_enabled(),
        }

        # Add coordinator status if running
        if self._coordinator:
            status["coordinator"] = self._coordinator.get_status()

        return status


# Global instance
_worker_manager: Optional[WorkerManager] = None


def get_worker_manager() -> WorkerManager:
    """Get or create the global worker manager."""
    global _worker_manager
    if _worker_manager is None:
        _worker_manager = WorkerManager()
    return _worker_manager
