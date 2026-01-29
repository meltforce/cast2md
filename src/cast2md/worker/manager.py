"""Worker manager for coordinating download and transcription workers."""

import logging
import threading
import time
from typing import Optional

from cast2md.config.settings import get_settings
from cast2md.db.connection import get_db, get_db_write
from cast2md.db.models import EpisodeStatus, Job, JobStatus, JobType
from cast2md.db.repository import EpisodeRepository, FeedRepository, JobRepository
from cast2md.download.downloader import download_episode
from cast2md.notifications.ntfy import (
    notify_download_failed,
    notify_transcription_complete,
    notify_transcription_failed,
)
from cast2md.storage.filesystem import ensure_podcast_directories, get_transcript_path
from cast2md.transcription.providers import TranscriptError, TranscriptResult, try_fetch_transcript
from cast2md.transcription.service import transcribe_episode

logger = logging.getLogger(__name__)


def _is_permanent_download_error(error_message: str) -> bool:
    """Check if a download error indicates a permanent failure (e.g., 401/403/404/410).

    These errors mean the audio file is inaccessible and retrying won't help.
    """
    return any(code in error_message for code in ("HTTP 401", "HTTP 403", "HTTP 404", "HTTP 410"))


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
        self._transcript_download_threads: list[threading.Thread] = []
        self._transcribe_thread: Optional[threading.Thread] = None
        self._embed_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._coordinator = None
        self._started_at: Optional[float] = None  # For startup grace period

        # Pause mechanism for transcript download workers
        self._tdl_pause_event = threading.Event()
        self._tdl_pause_event.set()  # Not paused initially
        self._tdl_pause_count = 0
        self._tdl_pause_lock = threading.Lock()

        settings = get_settings()
        self._max_download_workers = settings.max_concurrent_downloads
        self._max_transcript_download_workers = settings.max_transcript_download_workers

    def pause_transcript_downloads(self):
        """Pause transcript download workers. Use with try/finally to ensure resume."""
        with self._tdl_pause_lock:
            self._tdl_pause_count += 1
            self._tdl_pause_event.clear()
            logger.info(f"Transcript download workers paused (count: {self._tdl_pause_count})")

    def resume_transcript_downloads(self):
        """Resume transcript download workers."""
        with self._tdl_pause_lock:
            self._tdl_pause_count = max(0, self._tdl_pause_count - 1)
            if self._tdl_pause_count == 0:
                self._tdl_pause_event.set()
                logger.info("Transcript download workers resumed")
            else:
                logger.info(f"Transcript download workers still paused (count: {self._tdl_pause_count})")

    def start(self):
        """Start the worker threads."""
        if self._running:
            logger.warning("Workers already running")
            return

        self._running = True
        self._stop_event.clear()
        self._started_at = time.time()

        # Start download workers (audio downloads)
        for i in range(self._max_download_workers):
            thread = threading.Thread(
                target=self._download_worker,
                name=f"download-worker-{i}",
                daemon=True,
            )
            thread.start()
            self._download_threads.append(thread)
            logger.info(f"Started download worker {i}")

        # Start transcript download workers (fast, parallel)
        for i in range(self._max_transcript_download_workers):
            thread = threading.Thread(
                target=self._transcript_download_worker,
                name=f"transcript-download-worker-{i}",
                daemon=True,
            )
            thread.start()
            self._transcript_download_threads.append(thread)
            logger.info(f"Started transcript download worker {i}")

        # Start transcription worker (single, sequential)
        self._transcribe_thread = threading.Thread(
            target=self._transcribe_worker,
            name="transcribe-worker",
            daemon=True,
        )
        self._transcribe_thread.start()
        logger.info("Started transcription worker")

        # Start embedding worker (single, low priority background task)
        self._embed_thread = threading.Thread(
            target=self._embed_worker,
            name="embed-worker",
            daemon=True,
        )
        self._embed_thread.start()
        logger.info("Started embedding worker")

        # Start distributed transcription coordinator if enabled
        if _is_distributed_enabled():
            from cast2md.distributed import get_coordinator

            settings = get_settings()
            self._coordinator = get_coordinator()
            self._coordinator.configure(
                heartbeat_timeout_seconds=settings.node_heartbeat_timeout_seconds,
                job_timeout_minutes=settings.remote_job_timeout_minutes,
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

        # Calculate per-worker timeout
        total_workers = (
            len(self._download_threads)
            + len(self._transcript_download_threads)
            + (1 if self._transcribe_thread else 0)
            + (1 if self._embed_thread else 0)
        )
        per_worker_timeout = timeout / max(total_workers, 1)

        # Wait for download workers
        for thread in self._download_threads:
            thread.join(timeout=per_worker_timeout)

        # Wait for transcript download workers
        for thread in self._transcript_download_threads:
            thread.join(timeout=per_worker_timeout)

        # Wait for transcription worker
        if self._transcribe_thread:
            self._transcribe_thread.join(timeout=per_worker_timeout)

        # Wait for embedding worker
        if self._embed_thread:
            self._embed_thread.join(timeout=per_worker_timeout)

        self._download_threads.clear()
        self._transcript_download_threads.clear()
        self._transcribe_thread = None
        self._embed_thread = None
        logger.info("All workers stopped")

    def _download_worker(self):
        """Worker thread for processing download jobs."""
        while not self._stop_event.is_set():
            try:
                job = self._claim_next_job(JobType.DOWNLOAD)
                if job is None:
                    # No jobs, wait before checking again
                    self._stop_event.wait(timeout=5.0)
                    continue

                self._process_download_job(job.id, job.episode_id)

            except Exception as e:
                logger.error(f"Download worker error: {e}")
                time.sleep(5.0)

    def _transcribe_worker(self):
        """Worker thread for processing transcription jobs (sequential).

        When external workers (nodes or RunPod pods) are available, this worker
        defers to them and only helps with embedding jobs. When no external
        workers are available, it processes transcription jobs locally.

        This allows single-server users to transcribe out of the box while
        users with dedicated nodes/pods get better performance without
        server CPU overhead.
        """
        while not self._stop_event.is_set():
            try:
                # Check if external workers are available - defer to them
                if self._should_defer_transcription():
                    # External workers available - only help with embeddings
                    embed_job = self._claim_next_job(JobType.EMBED)
                    if embed_job is not None:
                        logger.debug("Local transcription in standby, helping with embed job")
                        self._process_embed_job(embed_job.id, embed_job.episode_id)
                        continue
                    # Wait longer when deferring (external workers handle transcription)
                    self._stop_event.wait(timeout=15.0)
                    continue

                job = self._claim_next_job(JobType.TRANSCRIBE)
                if job is None:
                    # No transcription jobs - try to help with embeddings
                    embed_job = self._claim_next_job(JobType.EMBED)
                    if embed_job is not None:
                        logger.debug("Transcribe worker helping with embed job")
                        self._process_embed_job(embed_job.id, embed_job.episode_id)
                        continue

                    # No jobs at all, wait before checking again
                    self._stop_event.wait(timeout=5.0)
                    continue

                self._process_transcribe_job(job.id, job.episode_id)

            except Exception as e:
                logger.error(f"Transcription worker error: {e}")
                time.sleep(5.0)

    def _should_defer_transcription(self) -> bool:
        """Check if local transcription should defer to external workers.

        Returns True if:
        - Within startup grace period (30s) when distributed transcription is enabled
        - External workers (nodes or RunPod pods) are available
        """
        if not _is_distributed_enabled():
            return False

        # Grace period after startup to let nodes send their first heartbeat
        # This prevents the server from grabbing jobs before nodes can announce
        startup_grace_seconds = 30
        if self._started_at and (time.time() - self._started_at) < startup_grace_seconds:
            logger.debug("Within startup grace period, deferring to potential external workers")
            return True

        try:
            from cast2md.distributed.coordinator import get_coordinator
            coordinator = get_coordinator()
            return coordinator.has_external_workers()
        except Exception as e:
            logger.debug(f"Error checking for external workers: {e}")
            return False

    def _transcript_download_worker(self):
        """Worker thread for processing transcript download jobs (fast, parallel)."""
        while not self._stop_event.is_set():
            try:
                # Wait if paused (with 60s timeout as safety net)
                if not self._tdl_pause_event.wait(timeout=60.0):
                    # Timeout - check stop event and continue waiting
                    continue

                job = self._claim_next_job(JobType.TRANSCRIPT_DOWNLOAD)
                if job is None:
                    # No jobs, wait before checking again
                    self._stop_event.wait(timeout=5.0)
                    continue

                self._process_transcript_download_job(job.id, job.episode_id)

            except Exception as e:
                logger.error(f"Transcript download worker error: {e}")
                time.sleep(5.0)

    def _embed_worker(self):
        """Worker thread for processing embedding jobs (low priority background task)."""
        while not self._stop_event.is_set():
            try:
                job = self._claim_next_job(JobType.EMBED)
                if job is None:
                    # No jobs, wait longer since embeddings are low priority
                    self._stop_event.wait(timeout=10.0)
                    continue

                self._process_embed_job(job.id, job.episode_id)

            except Exception as e:
                logger.error(f"Embedding worker error: {e}")
                time.sleep(5.0)

    def _claim_next_job(self, job_type: JobType) -> Optional[Job]:
        """Atomically claim the next available job from the queue.

        Uses UPDATE...RETURNING to prevent race conditions where multiple
        workers could claim the same job.

        For transcription jobs when distributed transcription is enabled,
        only claims jobs not assigned to remote nodes.
        """
        with get_db_write() as conn:
            repo = JobRepository(conn)
            # For transcription jobs with distributed enabled, only get unassigned jobs
            local_only = (
                job_type == JobType.TRANSCRIBE and _is_distributed_enabled()
            )
            return repo.claim_next_job(job_type, node_id="local", local_only=local_only)

    def _process_download_job(self, job_id: int, episode_id: int):
        """Process a download job."""
        logger.info(f"Processing download job {job_id} for episode {episode_id}")

        # Job is already marked as running by _claim_next_job
        with get_db_write() as conn:
            job_repo = JobRepository(conn)
            episode_repo = EpisodeRepository(conn)
            feed_repo = FeedRepository(conn)

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

            with get_db_write() as conn:
                job_repo = JobRepository(conn)
                job_repo.mark_completed(job_id)
                logger.info(f"Download job {job_id} completed")

                # Auto-queue transcription job
                self._queue_transcription(conn, episode_id)

        except Exception as e:
            error_str = str(e)
            logger.error(f"Download job {job_id} failed: {error_str}")
            permanent = _is_permanent_download_error(error_str)
            with get_db_write() as conn:
                job_repo = JobRepository(conn)
                episode_repo = EpisodeRepository(conn)
                job_repo.mark_failed(job_id, error_str, retry=not permanent)
                if permanent:
                    episode_repo.mark_permanent_failure(episode_id)

            # Send failure notification
            notify_download_failed(episode.title, feed.title, error_str)

    def _process_transcribe_job(self, job_id: int, episode_id: int):
        """Process a transcription job."""
        logger.info(f"Processing transcription job {job_id} for episode {episode_id}")

        # Job is already marked as running by _claim_next_job
        with get_db_write() as conn:
            job_repo = JobRepository(conn)
            episode_repo = EpisodeRepository(conn)
            feed_repo = FeedRepository(conn)

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
                    with get_db_write() as conn:
                        job_repo = JobRepository(conn)
                        job_repo.update_progress(job_id, progress)
                except Exception as e:
                    logger.debug(f"Failed to update progress for job {job_id}: {e}")

        try:
            # Perform the transcription (uses its own db connection)
            transcribe_episode(episode, feed, progress_callback=progress_callback)

            with get_db_write() as conn:
                job_repo = JobRepository(conn)
                job_repo.mark_completed(job_id)
                logger.info(f"Transcription job {job_id} completed")

                # Queue embedding job for semantic search
                self._queue_embedding(conn, episode_id)

            # Send success notification
            notify_transcription_complete(episode.title, feed.title)

        except Exception as e:
            import traceback
            error_detail = f"{type(e).__name__}: {e}"
            logger.error(
                f"Transcription job {job_id} failed for '{episode.title}': {error_detail}"
            )
            logger.debug(f"Traceback:\n{traceback.format_exc()}")
            with get_db_write() as conn:
                job_repo = JobRepository(conn)
                job_repo.mark_failed(job_id, error_detail)

            # Send failure notification
            notify_transcription_failed(episode.title, feed.title, error_detail)

    def _process_transcript_download_job(self, job_id: int, episode_id: int):
        """Process a transcript download job.

        Tries to download transcript from external providers (Podcast 2.0, Pocket Casts).
        If successful, saves the transcript and marks episode as COMPLETED.
        If 403 error, marks episode with retry info based on age.
        If no transcript is available, episode stays NEW for manual audio download.
        """
        from datetime import datetime, timedelta

        logger.info(f"Processing transcript download job {job_id} for episode {episode_id}")

        # Job is already marked as running by _claim_next_job
        with get_db_write() as conn:
            job_repo = JobRepository(conn)
            episode_repo = EpisodeRepository(conn)
            feed_repo = FeedRepository(conn)

            episode = episode_repo.get_by_id(episode_id)
            if not episode:
                job_repo.mark_failed(job_id, "Episode not found", retry=False)
                return

            feed = feed_repo.get_by_id(episode.feed_id)
            if not feed:
                job_repo.mark_failed(job_id, "Feed not found", retry=False)
                return

        try:
            # Try to fetch transcript from external providers
            result = try_fetch_transcript(episode, feed)
            now = datetime.now()

            if isinstance(result, TranscriptResult):
                # Save the downloaded transcript
                with get_db_write() as conn:
                    episode_repo = EpisodeRepository(conn)
                    job_repo = JobRepository(conn)

                    _, transcripts_dir = ensure_podcast_directories(feed.display_title)
                    transcript_path = get_transcript_path(
                        feed.display_title,
                        episode.title,
                        episode.published_at,
                    )

                    transcript_path.write_text(result.content, encoding="utf-8")

                    # Update episode with transcript info
                    episode_repo.update_transcript_from_download(
                        episode.id, str(transcript_path), result.source
                    )
                    episode_repo.update_status(episode.id, EpisodeStatus.COMPLETED)

                    # Index transcript for full-text search
                    try:
                        from cast2md.search.repository import TranscriptSearchRepository
                        search_repo = TranscriptSearchRepository(conn)
                        search_repo.index_episode(episode.id, str(transcript_path))
                    except Exception as index_error:
                        logger.warning(
                            f"Failed to index downloaded transcript for episode {episode.id}: {index_error}"
                        )

                    job_repo.mark_completed(job_id)

                    # Queue embedding job for semantic search
                    self._queue_embedding(conn, episode.id)

                logger.info(
                    f"Transcript download job {job_id} completed ({result.source}) for episode: {episode.title}"
                )

                # Send success notification
                notify_transcription_complete(episode.title, feed.title)

            elif isinstance(result, TranscriptError):
                # Got an error from provider (e.g., 403 forbidden)
                # Apply age-based status logic
                with get_db_write() as conn:
                    episode_repo = EpisodeRepository(conn)
                    job_repo = JobRepository(conn)

                    episode_age = timedelta(days=365)  # Default to old if no published date
                    if episode.published_at:
                        episode_age = now - episode.published_at

                    if episode_age < timedelta(days=7):
                        # New episode - might get transcript later, schedule retry
                        new_status = EpisodeStatus.AWAITING_TRANSCRIPT
                        next_retry = now + timedelta(hours=24)
                        logger.info(
                            f"Episode {episode.title} got {result.error_type}, "
                            f"will retry in 24h (age: {episode_age.days}d)"
                        )
                    else:
                        # Old episode - transcript won't appear, mark as unavailable
                        new_status = EpisodeStatus.NEEDS_AUDIO
                        next_retry = None
                        logger.info(
                            f"Episode {episode.title} got {result.error_type}, "
                            f"marking as unavailable (age: {episode_age.days}d)"
                        )

                    episode_repo.update_transcript_check(
                        episode.id,
                        status=new_status,
                        checked_at=now,
                        next_retry_at=next_retry,
                        failure_reason=result.error_type,
                    )
                    job_repo.mark_completed(job_id)

            else:
                # No transcript available from any provider (result is None)
                # For recent episodes, wait and retry - transcript may appear later
                # (e.g., Pocket Casts hasn't indexed the episode yet)
                # For older episodes, mark as needs_audio
                settings = get_settings()
                retry_threshold = timedelta(days=settings.transcript_retry_days)

                with get_db_write() as conn:
                    episode_repo = EpisodeRepository(conn)
                    job_repo = JobRepository(conn)

                    episode_age = timedelta(days=365)  # Default to old if no published date
                    if episode.published_at:
                        episode_age = now - episode.published_at

                    if episode_age < retry_threshold:
                        # Recent episode - transcript may appear later, schedule retry
                        new_status = EpisodeStatus.AWAITING_TRANSCRIPT
                        next_retry = now + timedelta(hours=24)
                        failure_reason = "no_external_transcript_yet"
                        logger.info(
                            f"Episode {episode.title} has no transcript yet, "
                            f"will retry in 24h (age: {episode_age.days}d)"
                        )
                    else:
                        # Older episode - unlikely to get external transcript
                        new_status = EpisodeStatus.NEEDS_AUDIO
                        next_retry = None
                        failure_reason = "no_external_transcript"
                        logger.info(
                            f"Marked episode as needs_audio (no external transcript): {episode.title}"
                        )

                    episode_repo.update_transcript_check(
                        episode.id,
                        status=new_status,
                        checked_at=now,
                        next_retry_at=next_retry,
                        failure_reason=failure_reason,
                    )
                    job_repo.mark_completed(job_id)

        except Exception as e:
            logger.error(f"Transcript download job {job_id} failed: {e}")
            # Use get_db_write with built-in retry logic
            try:
                with get_db_write() as conn:
                    job_repo = JobRepository(conn)
                    job_repo.mark_failed(job_id, str(e))
            except Exception as mark_error:
                logger.error(f"Failed to mark job {job_id} as failed: {mark_error}")

    def _process_embed_job(self, job_id: int, episode_id: int):
        """Process an embedding job for semantic search.

        Generates embeddings for an episode's transcript segments.
        """
        logger.info(f"Processing embedding job {job_id} for episode {episode_id}")

        # Job is already marked as running by _claim_next_job
        with get_db_write() as conn:
            job_repo = JobRepository(conn)
            episode_repo = EpisodeRepository(conn)

            episode = episode_repo.get_by_id(episode_id)
            if not episode:
                job_repo.mark_failed(job_id, "Episode not found", retry=False)
                return

            if not episode.transcript_path:
                job_repo.mark_failed(job_id, "Episode has no transcript", retry=False)
                return

        try:
            from cast2md.search.embeddings import is_embeddings_available
            from cast2md.search.repository import TranscriptSearchRepository

            if not is_embeddings_available():
                with get_db_write() as conn:
                    job_repo = JobRepository(conn)
                    job_repo.mark_failed(
                        job_id, "Embeddings not available (sentence-transformers not installed)", retry=False
                    )
                return

            with get_db_write() as conn:
                search_repo = TranscriptSearchRepository(conn)
                count = search_repo.index_episode_embeddings(
                    episode_id=episode_id,
                    transcript_path=episode.transcript_path,
                )

                job_repo = JobRepository(conn)
                job_repo.mark_completed(job_id)

            logger.info(
                f"Embedding job {job_id} completed: {count} segments embedded for episode {episode_id}"
            )

        except Exception as e:
            logger.error(f"Embedding job {job_id} failed: {e}")
            with get_db_write() as conn:
                job_repo = JobRepository(conn)
                job_repo.mark_failed(job_id, str(e))

    def _queue_transcription(self, conn, episode_id: int):
        """Queue a transcription job for an episode.

        First tries to download transcript from external providers (e.g., Podcast 2.0).
        Only queues Whisper transcription if no external transcript is available.
        """
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)
        job_repo = JobRepository(conn)

        # Check if already queued
        if job_repo.has_pending_job(episode_id, JobType.TRANSCRIBE):
            return

        episode = episode_repo.get_by_id(episode_id)
        if not episode:
            logger.warning(f"Episode {episode_id} not found")
            return

        feed = feed_repo.get_by_id(episode.feed_id)
        if not feed:
            logger.warning(f"Feed {episode.feed_id} not found")
            return

        # Try to fetch transcript from external providers
        result = try_fetch_transcript(episode, feed)
        if result:
            try:
                # Save the downloaded transcript
                _, transcripts_dir = ensure_podcast_directories(feed.title)
                transcript_path = get_transcript_path(
                    feed.title,
                    episode.title,
                    episode.published_at,
                )

                transcript_path.write_text(result.content, encoding="utf-8")

                # Update episode with transcript info
                episode_repo.update_transcript_from_download(
                    episode.id, str(transcript_path), result.source
                )
                episode_repo.update_status(episode.id, EpisodeStatus.COMPLETED)

                # Index transcript for full-text search
                try:
                    from cast2md.search.repository import TranscriptSearchRepository
                    search_repo = TranscriptSearchRepository(conn)
                    search_repo.index_episode(episode.id, str(transcript_path))
                except Exception as index_error:
                    logger.warning(
                        f"Failed to index downloaded transcript for episode {episode.id}: {index_error}"
                    )

                # Queue embedding job for semantic search
                self._queue_embedding(conn, episode.id)

                logger.info(
                    f"Downloaded transcript ({result.source}) for episode: {episode.title}"
                )

                # Send success notification
                notify_transcription_complete(episode.title, feed.title)
                return

            except Exception as e:
                logger.warning(
                    f"Failed to save downloaded transcript for episode {episode.title}: {e}"
                )
                # Fall through to queue Whisper transcription

        # No external transcript available, queue Whisper transcription
        job_repo.create(
            episode_id=episode_id,
            job_type=JobType.TRANSCRIBE,
            priority=1,  # High priority for newly downloaded
        )
        logger.info(f"Queued Whisper transcription for episode {episode_id}")

    def _queue_embedding(self, conn, episode_id: int):
        """Queue an embedding job for semantic search.

        Called after a transcript is created (transcription or download).
        Low priority since embeddings are not required for basic functionality.
        """
        from cast2md.db.connection import is_pgvector_available
        from cast2md.search.embeddings import is_embeddings_available

        # Check if embedding infrastructure is available
        embeddings_available = is_embeddings_available()
        pgvector_available = is_pgvector_available()
        if not embeddings_available or not pgvector_available:
            logger.debug(
                f"Skipping embedding for episode {episode_id}: "
                f"embeddings_available={embeddings_available}, pgvector_available={pgvector_available}"
            )
            return

        job_repo = JobRepository(conn)

        # Check if already queued
        if job_repo.has_pending_job(episode_id, JobType.EMBED):
            return

        job_repo.create(
            episode_id=episode_id,
            job_type=JobType.EMBED,
            priority=5,  # Medium priority for new transcripts
        )
        logger.debug(f"Queued embedding job for episode {episode_id}")

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
            transcript_download_counts = job_repo.count_by_status(JobType.TRANSCRIPT_DOWNLOAD)
            embed_counts = job_repo.count_by_status(JobType.EMBED)

            download_running = job_repo.get_running_jobs(JobType.DOWNLOAD)
            transcribe_running = job_repo.get_running_jobs(JobType.TRANSCRIBE)
            transcript_download_running = job_repo.get_running_jobs(JobType.TRANSCRIPT_DOWNLOAD)
            embed_running = job_repo.get_running_jobs(JobType.EMBED)

        status = {
            "running": self._running,
            "download_workers": len(self._download_threads),
            "transcript_download_workers": len(self._transcript_download_threads),
            "transcribe_workers": 1 if self._transcribe_thread else 0,
            "embed_workers": 1 if self._embed_thread else 0,
            "download_queue": {
                "queued": download_counts.get(JobStatus.QUEUED.value, 0),
                "running": len(download_running),
                "completed": download_counts.get(JobStatus.COMPLETED.value, 0),
                "failed": download_counts.get(JobStatus.FAILED.value, 0),
            },
            "transcript_download_queue": {
                "queued": transcript_download_counts.get(JobStatus.QUEUED.value, 0),
                "running": len(transcript_download_running),
                "completed": transcript_download_counts.get(JobStatus.COMPLETED.value, 0),
                "failed": transcript_download_counts.get(JobStatus.FAILED.value, 0),
            },
            "transcribe_queue": {
                "queued": transcribe_counts.get(JobStatus.QUEUED.value, 0),
                "running": len(transcribe_running),
                "completed": transcribe_counts.get(JobStatus.COMPLETED.value, 0),
                "failed": transcribe_counts.get(JobStatus.FAILED.value, 0),
            },
            "embed_queue": {
                "queued": embed_counts.get(JobStatus.QUEUED.value, 0),
                "running": len(embed_running),
                "completed": embed_counts.get(JobStatus.COMPLETED.value, 0),
                "failed": embed_counts.get(JobStatus.FAILED.value, 0),
            },
            "distributed_enabled": _is_distributed_enabled(),
            "transcribe_workers_standby": self._should_defer_transcription(),
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
