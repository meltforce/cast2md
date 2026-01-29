"""Transcriber node worker for processing remote transcription jobs."""

import logging
import os
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx


@dataclass
class PrefetchedJob:
    """A job that has been claimed and its audio downloaded, ready for transcription."""

    job: dict
    audio_path: Path
    temp_dir: tempfile.TemporaryDirectory

from cast2md.config.settings import get_settings
from cast2md.node.config import NodeConfig, load_config
from cast2md.storage.filesystem import cleanup_orphaned_temp_files
from cast2md.transcription.service import get_current_model_name, transcribe_audio

logger = logging.getLogger(__name__)


def _is_permanent_download_error(error: str) -> bool:
    """Check if a download error indicates a permanent failure (e.g., 404/410).

    These errors mean the audio file no longer exists and retrying won't help.
    """
    return "HTTP 404" in error or "HTTP 410" in error


def is_embeddings_available() -> bool:
    """Check if sentence-transformers is available for embedding generation."""
    try:
        from cast2md.search.embeddings import is_embeddings_available as check_embeddings
        return check_embeddings()
    except ImportError:
        return False


class TranscriberNodeWorker:
    """Worker that polls server for jobs and processes them.

    Responsibilities:
    - Poll server for jobs every 5 seconds
    - Download audio → transcribe → upload result
    - Send heartbeat every 30 seconds
    - Handle retries on network failure
    """

    def __init__(self, config: Optional[NodeConfig] = None):
        """Initialize the worker.

        Args:
            config: Node configuration. If None, loads from ~/.cast2md/node.json.
        """
        self._config = config or load_config()
        if not self._config:
            raise ValueError("No node configuration found. Run 'cast2md node register' first.")

        self._running = False
        self._stop_event = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None

        # Configurable intervals
        self._poll_interval = 5  # seconds
        self._heartbeat_interval = 30  # seconds

        # HTTP client
        self._client = httpx.Client(
            base_url=self._config.server_url,
            headers={"X-Transcriber-Key": self._config.api_key},
            timeout=30.0,
        )

        # Current job tracking
        self._current_job_id: Optional[int] = None
        self._current_episode_title: Optional[str] = None
        self._job_start_time: Optional[float] = None

        # Prefetch queue - keeps jobs ready for instant processing
        # With fast transcription (Parakeet), we need multiple jobs prefetched
        self._prefetch_queue: list[PrefetchedJob] = []
        self._prefetch_max_size = 3  # Maximum prefetched jobs
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: Optional[threading.Thread] = None
        self._prefetch_stop = threading.Event()  # Signal to stop prefetch thread
        self._prefetch_claimed_ids: set[int] = set()  # Job IDs claimed but still downloading

        # Auto-termination settings - both checks respect persistent/dev mode
        # Set NODE_PERSISTENT=1 to disable all auto-termination (dev mode / permanent workers)
        self._persistent = os.environ.get("NODE_PERSISTENT", "0") == "1"

        # Empty queue termination - matches CLI afterburner behavior
        # Terminate after N consecutive empty queue checks
        self._empty_queue_wait = int(os.environ.get("NODE_EMPTY_QUEUE_WAIT", "60"))  # seconds between checks
        self._required_empty_checks = int(os.environ.get("NODE_REQUIRED_EMPTY_CHECKS", "2"))
        self._consecutive_empty = 0

        # Idle timeout - safety net if jobs exist but can't be claimed
        # Default: 10 minutes
        self._idle_timeout_minutes = int(os.environ.get("NODE_IDLE_TIMEOUT_MINUTES", "10"))
        self._last_job_time: Optional[float] = None
        self._worker_start_time: float = time.time()

        # Server unreachable detection - terminate if server is down
        # Default: 5 minutes of failed heartbeats/claims
        self._server_unreachable_minutes = int(os.environ.get("NODE_SERVER_UNREACHABLE_MINUTES", "5"))
        self._last_server_contact: float = time.time()
        self._consecutive_server_failures = 0

        # Circuit breaker - terminate on consecutive transcription failures
        # Default: 3 consecutive failures triggers termination (0 to disable)
        self._max_consecutive_failures = int(os.environ.get("NODE_MAX_CONSECUTIVE_FAILURES", "3"))
        self._consecutive_failures = 0

    @property
    def config(self) -> NodeConfig:
        """Get the node configuration."""
        return self._config

    @property
    def is_running(self) -> bool:
        """Check if the worker is running."""
        return self._running

    @property
    def current_job(self) -> Optional[dict]:
        """Get current job info if any."""
        if self._current_job_id:
            elapsed_seconds = None
            if self._job_start_time:
                elapsed_seconds = int(time.time() - self._job_start_time)
            return {
                "job_id": self._current_job_id,
                "episode_title": self._current_episode_title,
                "elapsed_seconds": elapsed_seconds,
            }
        return None

    def start(self):
        """Start the worker threads."""
        if self._running:
            logger.warning("Worker already running")
            return

        # Clean up orphaned temp files from previous runs
        deleted = cleanup_orphaned_temp_files(hours=24)
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} orphaned temp files")

        self._running = True
        self._stop_event.clear()

        # Start heartbeat thread
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="node-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()
        logger.info("Started heartbeat thread")

        # Start job poll thread
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name="node-poll",
            daemon=True,
        )
        self._poll_thread.start()
        logger.info("Started job poll thread")

        # Start prefetch thread
        self._prefetch_thread = threading.Thread(
            target=self._prefetch_loop,
            name="node-prefetch",
            daemon=True,
        )
        self._prefetch_thread.start()
        logger.info("Started prefetch thread")

    def stop(self, timeout: float = 30.0):
        """Stop the worker gracefully."""
        if not self._running:
            return

        logger.info("Stopping worker...")
        self._stop_event.set()
        self._prefetch_stop.set()
        self._running = False

        # If we have a current job, notify server to release it
        if self._current_job_id:
            try:
                self._release_current_job()
            except Exception as e:
                logger.warning(f"Failed to release job on shutdown: {e}")

        # Release all prefetched jobs
        self._release_all_prefetched_jobs()

        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=timeout / 3)
        if self._poll_thread:
            self._poll_thread.join(timeout=timeout / 3)
        if self._prefetch_thread:
            self._prefetch_thread.join(timeout=timeout / 3)

        self._heartbeat_thread = None
        self._poll_thread = None
        self._prefetch_thread = None
        self._client.close()
        logger.info("Worker stopped")

    def _release_current_job(self):
        """Notify server to release our current job back to queue."""
        if not self._current_job_id:
            return

        logger.info(f"Releasing job {self._current_job_id} back to queue...")
        try:
            response = self._client.post(
                f"/api/nodes/jobs/{self._current_job_id}/release",
                timeout=5.0,
            )
            if response.status_code == 200:
                logger.info(f"Released job {self._current_job_id} back to queue")
            else:
                logger.warning(f"Failed to release job: {response.status_code}")
        except httpx.RequestError as e:
            logger.warning(f"Failed to release job: {e}")
        finally:
            self._current_job_id = None

    def _release_all_prefetched_jobs(self):
        """Release all prefetched jobs back to queue."""
        with self._prefetch_lock:
            jobs_to_release = list(self._prefetch_queue)
            self._prefetch_queue.clear()
            self._prefetch_claimed_ids.clear()

        for prefetched in jobs_to_release:
            job_id = prefetched.job.get("job_id")
            if job_id:
                logger.info(f"Releasing prefetched job {job_id}")
                try:
                    self._client.post(f"/api/nodes/jobs/{job_id}/release", timeout=5.0)
                except Exception as e:
                    logger.warning(f"Failed to release prefetch job {job_id}: {e}")
            try:
                prefetched.temp_dir.cleanup()
            except Exception:
                pass

    def run(self):
        """Run the worker (blocking)."""
        self.start()
        logger.info(f"Node '{self._config.name}' started, polling {self._config.server_url}")

        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        finally:
            self.stop()

    def _heartbeat_loop(self):
        """Send periodic heartbeats to the server."""
        settings = get_settings()
        model_name = get_current_model_name()

        while not self._stop_event.is_set():
            try:
                # Collect all claimed job IDs (current + prefetch queue + in-flight downloads)
                claimed_job_ids = []
                if self._current_job_id:
                    claimed_job_ids.append(self._current_job_id)
                with self._prefetch_lock:
                    for prefetched in self._prefetch_queue:
                        job_id = prefetched.job.get("job_id")
                        if job_id:
                            claimed_job_ids.append(job_id)
                    for job_id in self._prefetch_claimed_ids:
                        if job_id not in claimed_job_ids:
                            claimed_job_ids.append(job_id)

                response = self._client.post(
                    f"/api/nodes/{self._config.node_id}/heartbeat",
                    json={
                        "name": self._config.name,
                        "whisper_model": model_name,
                        "whisper_backend": settings.transcription_backend,
                        "current_job_id": self._current_job_id,
                        "claimed_job_ids": claimed_job_ids,
                    },
                )
                if response.status_code == 200:
                    logger.debug("Heartbeat sent")
                    # Server responded - update last contact time
                    self._last_server_contact = time.time()
                else:
                    logger.warning(f"Heartbeat failed: {response.status_code}")
            except httpx.RequestError as e:
                logger.warning(f"Heartbeat error: {e}")

            self._stop_event.wait(timeout=self._heartbeat_interval)

    def _check_should_terminate(self) -> tuple[bool, str]:
        """Check if worker should auto-terminate.

        Four conditions checked (all respect persistent/dev mode):
        1. Empty queue - no claimable jobs for N consecutive checks
        2. Idle timeout - no jobs processed for M minutes (safety net)
        3. Server unreachable - can't reach server for K minutes
        4. Circuit breaker - N consecutive transcription failures (broken GPU)

        Returns:
            Tuple of (should_terminate, reason)
        """
        # Log loud warning in persistent mode but don't terminate
        if self._persistent and self._max_consecutive_failures > 0:
            if self._consecutive_failures >= self._max_consecutive_failures:
                logger.error(
                    f"CIRCUIT BREAKER: {self._consecutive_failures} consecutive failures "
                    f"(not terminating - persistent/dev mode)"
                )

        if self._persistent:
            return False, ""  # Dev mode - never auto-terminate

        # Check 1: Empty queue (matches CLI afterburner behavior)
        if self._consecutive_empty >= self._required_empty_checks:
            return True, f"queue empty for {self._consecutive_empty} consecutive checks"

        # Check 2: Idle timeout (safety net for stuck jobs)
        if self._idle_timeout_minutes > 0:
            reference_time = self._last_job_time or self._worker_start_time
            idle_minutes = (time.time() - reference_time) / 60
            if idle_minutes >= self._idle_timeout_minutes:
                return True, f"idle for {int(idle_minutes)} minutes (no jobs processed)"

        # Check 3: Server unreachable (server crash protection)
        if self._server_unreachable_minutes > 0:
            unreachable_minutes = (time.time() - self._last_server_contact) / 60
            if unreachable_minutes >= self._server_unreachable_minutes:
                return True, f"server unreachable for {int(unreachable_minutes)} minutes"

        # Check 4: Consecutive transcription failures (broken GPU protection)
        if self._max_consecutive_failures > 0:
            if self._consecutive_failures >= self._max_consecutive_failures:
                return True, (
                    f"{self._consecutive_failures} consecutive transcription failures"
                )

        return False, ""

    def _request_server_termination(self, reason: str):
        """Request the server to terminate our pod.

        For RunPod workers, this allows the server to terminate the pod
        and clean up state atomically, preventing orphaned setup states.

        Args:
            reason: The termination reason to log
        """
        try:
            logger.info(f"Requesting server to terminate pod (reason: {reason})")
            response = self._client.post(
                f"/api/nodes/{self._config.node_id}/request-termination",
                timeout=10.0,
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("terminated"):
                    logger.info(f"Server confirmed termination: {data.get('message')}")
                else:
                    # Not a RunPod node or service unavailable - that's ok
                    logger.info(f"Server response: {data.get('message')}")
            else:
                logger.warning(f"Termination request failed: {response.status_code}")
        except httpx.RequestError as e:
            # Server might be unreachable (which is one termination reason)
            logger.warning(f"Failed to request termination: {e}")

    def _poll_loop(self):
        """Poll for jobs and process them."""
        while not self._stop_event.is_set():
            try:
                # Check for prefetched job first
                prefetched = None
                with self._prefetch_lock:
                    if self._prefetch_queue:
                        prefetched = self._prefetch_queue.pop(0)

                if prefetched:
                    logger.info(f"Using prefetched job {prefetched.job['job_id']}")
                    self._consecutive_empty = 0  # Reset empty counter
                    self._process_prefetched_job(
                        prefetched.job, prefetched.audio_path, prefetched.temp_dir
                    )
                    # Check circuit breaker after job
                    should_terminate, reason = self._check_should_terminate()
                    if should_terminate:
                        logger.warning(f"Initiating shutdown: {reason}")
                        self._request_server_termination(reason)
                        self._stop_event.set()
                        self._running = False
                        break
                else:
                    # No prefetched job, claim and process directly
                    job = self._claim_job()
                    if job:
                        self._consecutive_empty = 0  # Reset empty counter
                        self._process_job(job)
                        # Check circuit breaker after job
                        should_terminate, reason = self._check_should_terminate()
                        if should_terminate:
                            logger.warning(f"Initiating shutdown: {reason}")
                            self._request_server_termination(reason)
                            self._stop_event.set()
                            self._running = False
                            break
                    else:
                        # No transcription job - try to help with embeddings
                        if is_embeddings_available():
                            embed_job = self._claim_embed_job()
                            if embed_job:
                                self._consecutive_empty = 0  # Reset empty counter
                                logger.info("Helping with embed job (no transcription work)")
                                self._process_embed_job(embed_job)
                                continue

                        # No job available - track for empty queue termination
                        self._consecutive_empty += 1
                        if self._consecutive_empty == 1:
                            logger.info("Queue appears empty, waiting to confirm...")
                        elif self._consecutive_empty < self._required_empty_checks:
                            logger.info(
                                f"Queue still empty ({self._consecutive_empty}/{self._required_empty_checks})..."
                            )

                        # Check all termination conditions
                        should_terminate, reason = self._check_should_terminate()
                        if should_terminate:
                            logger.warning(f"Initiating shutdown: {reason}")
                            # Request server to terminate our pod (if RunPod worker)
                            # This allows the server to clean up state atomically
                            self._request_server_termination(reason)
                            self._stop_event.set()
                            self._running = False
                            break

                        # Wait before next poll (use empty_queue_wait when checking for empty)
                        wait_time = self._empty_queue_wait if self._consecutive_empty > 0 else self._poll_interval
                        self._stop_event.wait(timeout=wait_time)

            except Exception as e:
                logger.error(f"Poll loop error: {e}")
                self._stop_event.wait(timeout=self._poll_interval)

    def _prefetch_loop(self):
        """Continuously prefetch jobs to keep the queue full.

        This runs independently of the main processing loop, ensuring
        audio is always ready for instant transcription.
        """
        while not self._prefetch_stop.is_set():
            try:
                # Check if we need more prefetched jobs
                with self._prefetch_lock:
                    queue_size = len(self._prefetch_queue)

                if queue_size >= self._prefetch_max_size:
                    # Queue is full, wait a bit
                    self._prefetch_stop.wait(timeout=1.0)
                    continue

                # Try to claim and download next job
                job = self._claim_job()
                if not job:
                    # No jobs available, wait before trying again
                    self._prefetch_stop.wait(timeout=self._poll_interval)
                    continue

                job_id = job['job_id']
                logger.info(
                    f"Prefetching job {job_id}: {job.get('episode_title', 'Unknown')}"
                    f" (queue: {queue_size}/{self._prefetch_max_size})"
                )

                # Track as in-flight so heartbeat reports it to server
                with self._prefetch_lock:
                    self._prefetch_claimed_ids.add(job_id)

                # Create temp dir for prefetch
                temp_dir = tempfile.TemporaryDirectory()
                audio_path, error = self._download_audio(job["audio_url"], Path(temp_dir.name))

                if error or not audio_path:
                    logger.warning(f"Prefetch download failed for job {job_id}: {error}")
                    with self._prefetch_lock:
                        self._prefetch_claimed_ids.discard(job_id)
                    # Fail the job (not release) - permanent for 404/410, retryable otherwise
                    permanent = _is_permanent_download_error(error or "")
                    self._fail_job(
                        job_id,
                        f"Failed to download audio: {error}",
                        permanent=permanent,
                    )
                    temp_dir.cleanup()
                else:
                    with self._prefetch_lock:
                        self._prefetch_claimed_ids.discard(job_id)
                        self._prefetch_queue.append(
                            PrefetchedJob(job=job, audio_path=audio_path, temp_dir=temp_dir)
                        )
                    logger.info(
                        f"Prefetch ready: {job_id} "
                        f"(queue: {len(self._prefetch_queue)}/{self._prefetch_max_size})"
                    )

            except Exception as e:
                logger.warning(f"Prefetch error: {e}")
                self._prefetch_stop.wait(timeout=self._poll_interval)

    def _claim_job(self) -> Optional[dict]:
        """Try to claim a job from the server.

        Returns:
            Job info dict if claimed, None otherwise.
        """
        try:
            response = self._client.post(f"/api/nodes/{self._config.node_id}/claim")

            if response.status_code != 200:
                logger.warning(f"Claim request failed: {response.status_code}")
                return None

            # Server responded - update last contact time
            self._last_server_contact = time.time()

            data = response.json()
            if not data.get("has_job"):
                return None

            logger.info(f"Claimed job {data['job_id']}: {data.get('episode_title', 'Unknown')}")
            return data

        except httpx.RequestError as e:
            logger.warning(f"Claim error: {e}")
            return None

    def _process_job(self, job: dict):
        """Process a claimed job.

        Args:
            job: Job info from claim response.
        """
        job_id = job["job_id"]
        episode_title = job.get("episode_title", "Unknown")
        audio_url = job["audio_url"]

        self._current_job_id = job_id
        self._current_episode_title = episode_title
        self._job_start_time = time.time()

        try:
            logger.info(f"Processing job {job_id}: {episode_title}")

            # Create temp directory for this job
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)

                # Download audio
                audio_path, download_error = self._download_audio(audio_url, temp_path)
                if download_error:
                    permanent = _is_permanent_download_error(download_error)
                    self._fail_job(
                        job_id,
                        f"Failed to download audio: {download_error}",
                        permanent=permanent,
                    )
                    return
                if not audio_path:
                    self._fail_job(job_id, "Download returned no path")
                    return

                # Transcribe
                transcript, error = self._transcribe(audio_path, job_id)

                if error:
                    self._fail_job(job_id, f"Transcription failed: {error}")
                    return
                if not transcript:
                    self._fail_job(job_id, "Transcription returned empty result")
                    return

                # Upload result
                self._complete_job(job_id, transcript)

        except Exception as e:
            logger.error(f"Job {job_id} failed with exception: {e}")
            self._fail_job(job_id, str(e))
        finally:
            self._current_job_id = None
            self._current_episode_title = None
            self._job_start_time = None

    def _process_prefetched_job(
        self, job: dict, audio_path: Path, temp_dir: tempfile.TemporaryDirectory
    ):
        """Process a prefetched job (audio already downloaded).

        Args:
            job: Job info from claim response.
            audio_path: Path to already-downloaded audio file.
            temp_dir: Temp directory containing the audio (will be cleaned up).
        """
        job_id = job["job_id"]
        episode_title = job.get("episode_title", "Unknown")

        self._current_job_id = job_id
        self._current_episode_title = episode_title
        self._job_start_time = time.time()

        try:
            logger.info(f"Processing prefetched job {job_id}: {episode_title}")

            transcript, error = self._transcribe(audio_path, job_id)

            if error:
                self._fail_job(job_id, f"Transcription failed: {error}")
                return
            if not transcript:
                self._fail_job(job_id, "Transcription returned empty result")
                return

            self._complete_job(job_id, transcript)

        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}")
            self._fail_job(job_id, str(e))
        finally:
            self._current_job_id = None
            self._current_episode_title = None
            self._job_start_time = None
            temp_dir.cleanup()

    def _download_audio(self, audio_url: str, temp_dir: Path) -> tuple[Optional[Path], Optional[str]]:
        """Download audio file from server.

        Args:
            audio_url: Relative URL to audio file.
            temp_dir: Directory to save audio to.

        Returns:
            Tuple of (path to downloaded file, error message). One will be None.
        """
        logger.info(f"Downloading audio from {audio_url}")

        try:
            # Stream download to handle large files
            with self._client.stream("GET", audio_url) as response:
                if response.status_code != 200:
                    error = f"HTTP {response.status_code}"
                    logger.error(f"Download failed: {error}")
                    return None, error

                # Get filename from content-disposition or use default
                filename = "audio.mp3"
                if "content-disposition" in response.headers:
                    cd = response.headers["content-disposition"]
                    if "filename=" in cd:
                        filename = cd.split("filename=")[1].strip('"')

                audio_path = temp_dir / filename

                with open(audio_path, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=8192):
                        f.write(chunk)

            logger.info(f"Downloaded to {audio_path} ({audio_path.stat().st_size} bytes)")
            return audio_path, None

        except httpx.RequestError as e:
            error = f"{type(e).__name__}: {e}"
            logger.error(f"Download error: {error}")
            return None, error

    def _transcribe(self, audio_path: Path, job_id: int) -> tuple[Optional[str], Optional[str]]:
        """Transcribe audio file.

        Args:
            audio_path: Path to audio file.
            job_id: Job ID for progress reporting.

        Returns:
            Tuple of (transcript text, error message). One will be None.
        """
        logger.info(f"Transcribing {audio_path}")

        # Create progress callback that reports to server
        last_progress = [0]
        last_report_time = [time.time()]

        def progress_callback(progress: int):
            # Throttle progress updates to every 5 seconds
            now = time.time()
            if progress > last_progress[0] + 5 or (now - last_report_time[0]) >= 5:
                last_progress[0] = progress
                last_report_time[0] = now
                self._report_progress(job_id, progress)

        try:
            # Use the same transcription service as the main server
            transcript = transcribe_audio(
                str(audio_path),
                include_timestamps=True,
                progress_callback=progress_callback,
            )
            logger.info(f"Transcription complete ({len(transcript)} chars)")
            return transcript, None

        except Exception as e:
            import traceback
            error_detail = f"{type(e).__name__}: {e}"
            logger.error(f"Transcription error: {error_detail}")
            logger.debug(f"Traceback:\n{traceback.format_exc()}")
            return None, error_detail

    def _report_progress(self, job_id: int, progress: int):
        """Report progress to server.

        Args:
            job_id: Job ID.
            progress: Progress percentage (0-100).
        """
        try:
            response = self._client.post(
                f"/api/nodes/jobs/{job_id}/progress",
                json={"progress_percent": progress},
                timeout=10.0,
            )
            if response.status_code == 200:
                logger.debug(f"Progress reported: {progress}%")
            else:
                logger.warning(f"Progress report failed: {response.status_code}")
        except httpx.RequestError as e:
            logger.debug(f"Progress report error: {e}")

    def _complete_job(self, job_id: int, transcript: str):
        """Submit completed job to server.

        Args:
            job_id: Job ID to complete.
            transcript: Transcript text.
        """
        logger.info(f"Completing job {job_id}")
        model_name = get_current_model_name()

        try:
            response = self._client.post(
                f"/api/nodes/jobs/{job_id}/complete",
                json={
                    "transcript_text": transcript,
                    "whisper_model": model_name,
                },
                timeout=60.0,  # Longer timeout for large transcripts
            )

            if response.status_code == 200:
                logger.info(f"Job {job_id} completed successfully")
                # Reset idle timer on successful completion
                self._last_job_time = time.time()
                # Reset circuit breaker on success
                self._consecutive_failures = 0
            else:
                logger.error(f"Complete request failed: {response.status_code} - {response.text}")

        except httpx.RequestError as e:
            logger.error(f"Complete error: {e}")
            # TODO: Store locally and retry on restart

    def _fail_job(self, job_id: int, error_message: str, permanent: bool = False):
        """Report job failure to server.

        Args:
            job_id: Job ID that failed.
            error_message: Error description.
            permanent: If True, marks as permanent failure (no retry, e.g., 404/410).
        """
        if permanent:
            logger.warning(f"Permanently failing job {job_id}: {error_message}")
        else:
            logger.warning(f"Failing job {job_id}: {error_message}")

        # Track consecutive failures for circuit breaker
        self._consecutive_failures += 1
        if self._consecutive_failures > 1:
            logger.warning(
                f"Consecutive failures: {self._consecutive_failures}"
                f"/{self._max_consecutive_failures}"
            )

        try:
            response = self._client.post(
                f"/api/nodes/jobs/{job_id}/fail",
                json={
                    "error_message": error_message,
                    "retry": not permanent,
                },
            )

            if response.status_code == 200:
                logger.info(f"Job {job_id} marked as failed")
                # Reset idle timer even on failure (we did work)
                self._last_job_time = time.time()
            else:
                logger.error(f"Fail request failed: {response.status_code}")

        except httpx.RequestError as e:
            logger.error(f"Fail error: {e}")

    # === Embedding Job Support ===

    def _claim_embed_job(self) -> Optional[dict]:
        """Try to claim an embed job from the server.

        Returns:
            Job info dict if claimed, None otherwise.
        """
        try:
            response = self._client.post(f"/api/nodes/{self._config.node_id}/claim-embed")

            if response.status_code != 200:
                logger.debug(f"Embed claim request failed: {response.status_code}")
                return None

            # Server responded - update last contact time
            self._last_server_contact = time.time()

            data = response.json()
            if not data.get("has_job"):
                return None

            logger.info(f"Claimed embed job {data['job_id']}: {data.get('episode_title', 'Unknown')}")
            return data

        except httpx.RequestError as e:
            logger.debug(f"Embed claim error: {e}")
            return None

    def _process_embed_job(self, job: dict):
        """Process an embed job: download transcript, generate embeddings, upload.

        Args:
            job: Job info from claim-embed response.
        """
        job_id = job["job_id"]
        episode_title = job.get("episode_title", "Unknown")
        transcript_url = job["transcript_url"]

        try:
            logger.info(f"Processing embed job {job_id}: {episode_title}")

            # Download transcript content
            transcript_content = self._download_transcript(transcript_url)
            if not transcript_content:
                self._fail_job(job_id, "Failed to download transcript")
                return

            # Generate embeddings
            embeddings = self._generate_embeddings(transcript_content)
            if not embeddings:
                self._fail_job(job_id, "Failed to generate embeddings")
                return

            # Upload embeddings to server
            self._complete_embed_job(job_id, embeddings)

        except Exception as e:
            logger.error(f"Embed job {job_id} failed with exception: {e}")
            self._fail_job(job_id, str(e))

    def _download_transcript(self, transcript_url: str) -> Optional[str]:
        """Download transcript content from server.

        Args:
            transcript_url: Relative URL to transcript endpoint.

        Returns:
            Transcript content string, or None on error.
        """
        try:
            response = self._client.get(transcript_url, timeout=30.0)
            if response.status_code != 200:
                logger.error(f"Transcript download failed: HTTP {response.status_code}")
                return None

            data = response.json()
            return data.get("transcript_content")

        except httpx.RequestError as e:
            logger.error(f"Transcript download error: {e}")
            return None

    def _generate_embeddings(self, transcript_content: str) -> Optional[list[dict]]:
        """Generate embeddings for transcript segments.

        Args:
            transcript_content: Raw transcript content (markdown string).

        Returns:
            List of embedding dicts, or None on error.
        """
        from cast2md.search.embeddings import generate_embeddings_batch
        from cast2md.search.parser import (
            TranscriptSegment,
            merge_word_level_segments,
            parse_transcript_segments,
        )

        try:
            # Parse transcript (markdown format with **[MM:SS]** timestamps)
            segments = parse_transcript_segments(transcript_content)

            # If no timestamped segments found, treat as plain text
            if not segments:
                segments = [TranscriptSegment(start=0.0, end=0.0, text=transcript_content)]

            # Merge word-level segments into phrases
            segments = merge_word_level_segments(segments)

            if not segments:
                logger.warning("No segments found in transcript")
                return []

            # Generate embeddings in batch (as numpy arrays, then convert to lists for JSON)
            texts = [seg.text for seg in segments]
            embeddings = generate_embeddings_batch(texts, as_numpy=True)

            # Build result list
            result = []
            for i, (segment, embedding) in enumerate(zip(segments, embeddings)):
                result.append({
                    "segment_index": i,
                    "text": segment.text,
                    "start": segment.start,
                    "end": segment.end,
                    "embedding": embedding.tolist(),
                })

            logger.info(f"Generated {len(result)} embeddings")
            return result

        except Exception as e:
            logger.error(f"Embedding generation error: {e}")
            import traceback
            logger.debug(f"Traceback:\n{traceback.format_exc()}")
            return None

    def _complete_embed_job(self, job_id: int, embeddings: list[dict]):
        """Submit completed embed job to server.

        Args:
            job_id: Job ID to complete.
            embeddings: List of embedding dicts.
        """
        logger.info(f"Completing embed job {job_id} ({len(embeddings)} embeddings)")

        try:
            response = self._client.post(
                f"/api/nodes/jobs/{job_id}/complete-embed",
                json={"embeddings": embeddings},
                timeout=120.0,  # Longer timeout for large uploads
            )

            if response.status_code == 200:
                logger.info(f"Embed job {job_id} completed successfully")
                # Reset idle timer on successful completion
                self._last_job_time = time.time()
            else:
                logger.error(f"Complete embed request failed: {response.status_code} - {response.text}")

        except httpx.RequestError as e:
            logger.error(f"Complete embed error: {e}")
