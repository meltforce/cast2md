"""Transcriber node worker for processing remote transcription jobs."""

import logging
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from cast2md.config.settings import get_settings
from cast2md.node.config import NodeConfig, load_config
from cast2md.transcription.service import transcribe_audio

logger = logging.getLogger(__name__)


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

        # Prefetch state
        self._prefetch_job: Optional[dict] = None
        self._prefetch_audio_path: Optional[Path] = None
        self._prefetch_temp_dir: Optional[tempfile.TemporaryDirectory] = None
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: Optional[threading.Thread] = None
        self._transcribing = threading.Event()  # Signal when transcription starts

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
        self._running = False

        # If we have a current job, notify server to release it
        if self._current_job_id:
            try:
                self._release_current_job()
            except Exception as e:
                logger.warning(f"Failed to release job on shutdown: {e}")

        # Release prefetched job if any
        self._release_prefetch_job()

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

    def _release_prefetch_job(self):
        """Release prefetched job back to queue."""
        with self._prefetch_lock:
            if not self._prefetch_job:
                return
            job_id = self._prefetch_job.get("job_id")

        if job_id:
            logger.info(f"Releasing prefetched job {job_id}")
            try:
                self._client.post(f"/api/nodes/jobs/{job_id}/release", timeout=5.0)
            except Exception as e:
                logger.warning(f"Failed to release prefetch job: {e}")

        with self._prefetch_lock:
            self._prefetch_job = None
            self._prefetch_audio_path = None
            if self._prefetch_temp_dir:
                self._prefetch_temp_dir.cleanup()
                self._prefetch_temp_dir = None

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

        while not self._stop_event.is_set():
            try:
                response = self._client.post(
                    f"/api/nodes/{self._config.node_id}/heartbeat",
                    json={
                        "whisper_model": settings.whisper_model,
                        "whisper_backend": settings.whisper_backend,
                    },
                )
                if response.status_code == 200:
                    logger.debug("Heartbeat sent")
                else:
                    logger.warning(f"Heartbeat failed: {response.status_code}")
            except httpx.RequestError as e:
                logger.warning(f"Heartbeat error: {e}")

            self._stop_event.wait(timeout=self._heartbeat_interval)

    def _poll_loop(self):
        """Poll for jobs and process them."""
        while not self._stop_event.is_set():
            try:
                # Check for prefetched job first
                job = None
                prefetch_audio = None
                prefetch_temp = None

                with self._prefetch_lock:
                    if self._prefetch_job and self._prefetch_audio_path:
                        job = self._prefetch_job
                        prefetch_audio = self._prefetch_audio_path
                        prefetch_temp = self._prefetch_temp_dir
                        self._prefetch_job = None
                        self._prefetch_audio_path = None
                        self._prefetch_temp_dir = None

                if job and prefetch_audio and prefetch_temp:
                    logger.info(f"Using prefetched job {job['job_id']}")
                    self._process_prefetched_job(job, prefetch_audio, prefetch_temp)
                else:
                    job = self._claim_job()
                    if job:
                        self._process_job(job)
                    else:
                        # No job available, wait before polling again
                        self._stop_event.wait(timeout=self._poll_interval)
            except Exception as e:
                logger.error(f"Poll loop error: {e}")
                self._stop_event.wait(timeout=self._poll_interval)

    def _prefetch_loop(self):
        """Prefetch next job's audio while current job is transcribing."""
        while not self._stop_event.is_set():
            # Wait for transcription to start
            self._transcribing.wait(timeout=1.0)
            if self._stop_event.is_set():
                break
            if not self._transcribing.is_set():
                continue

            # Check if we already have a prefetch
            with self._prefetch_lock:
                if self._prefetch_job is not None:
                    self._transcribing.clear()
                    continue

            # Try to claim and download next job
            try:
                job = self._claim_job()
                if not job:
                    self._transcribing.clear()
                    continue

                logger.info(f"Prefetching job {job['job_id']}: {job.get('episode_title', 'Unknown')}")

                # Create temp dir for prefetch
                temp_dir = tempfile.TemporaryDirectory()
                audio_path, error = self._download_audio(job["audio_url"], Path(temp_dir.name))

                if error or not audio_path:
                    logger.warning(f"Prefetch download failed: {error}")
                    # Release the job we claimed
                    try:
                        self._client.post(f"/api/nodes/jobs/{job['job_id']}/release", timeout=5.0)
                    except Exception:
                        pass
                    temp_dir.cleanup()
                else:
                    with self._prefetch_lock:
                        self._prefetch_job = job
                        self._prefetch_audio_path = audio_path
                        self._prefetch_temp_dir = temp_dir
                    logger.info(f"Prefetch ready: {job['job_id']}")

            except Exception as e:
                logger.warning(f"Prefetch error: {e}")

            self._transcribing.clear()

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
                    self._fail_job(job_id, f"Failed to download audio: {download_error}")
                    return
                if not audio_path:
                    self._fail_job(job_id, "Download returned no path")
                    return

                # Signal that transcription is starting (for prefetch)
                self._transcribing.set()

                # Transcribe
                transcript, error = self._transcribe(audio_path, job_id)

                # Clear transcribing signal
                self._transcribing.clear()

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

            # Signal transcription starting (for next prefetch)
            self._transcribing.set()

            transcript, error = self._transcribe(audio_path, job_id)

            self._transcribing.clear()

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
        settings = get_settings()

        try:
            response = self._client.post(
                f"/api/nodes/jobs/{job_id}/complete",
                json={
                    "transcript_text": transcript,
                    "whisper_model": settings.whisper_model,
                },
                timeout=60.0,  # Longer timeout for large transcripts
            )

            if response.status_code == 200:
                logger.info(f"Job {job_id} completed successfully")
            else:
                logger.error(f"Complete request failed: {response.status_code} - {response.text}")

        except httpx.RequestError as e:
            logger.error(f"Complete error: {e}")
            # TODO: Store locally and retry on restart

    def _fail_job(self, job_id: int, error_message: str):
        """Report job failure to server.

        Args:
            job_id: Job ID that failed.
            error_message: Error description.
        """
        logger.warning(f"Failing job {job_id}: {error_message}")

        try:
            response = self._client.post(
                f"/api/nodes/jobs/{job_id}/fail",
                json={"error_message": error_message},
            )

            if response.status_code == 200:
                logger.info(f"Job {job_id} marked as failed")
            else:
                logger.error(f"Fail request failed: {response.status_code}")

        except httpx.RequestError as e:
            logger.error(f"Fail error: {e}")
