"""Background scheduler for feed polling and transcript retries."""

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from cast2md.db.connection import get_db
from cast2md.db.models import EpisodeStatus, JobType
from cast2md.db.repository import EpisodeRepository, FeedRepository, JobRepository
from cast2md.feed.discovery import discover_new_episodes

logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler: BackgroundScheduler | None = None


def poll_all_feeds():
    """Poll all feeds for new episodes and auto-queue them."""
    logger.info("Starting scheduled feed poll")

    with get_db() as conn:
        repo = FeedRepository(conn)
        feeds = repo.get_all()

    total_new = 0
    total_queued = 0
    for feed in feeds:
        try:
            result = discover_new_episodes(feed, auto_queue=True, queue_only_latest=False)
            if result.total_new > 0:
                logger.info(f"Feed '{feed.title}': {result.total_new} new episodes, {len(result.new_episode_ids)} queued")
                total_new += result.total_new
                total_queued += len(result.new_episode_ids)
        except Exception as e:
            logger.error(f"Failed to poll feed '{feed.title}': {e}")

    logger.info(f"Feed poll complete. Total new episodes: {total_new}, queued: {total_queued}")


def check_auto_scale():
    """Check if we should auto-scale RunPod workers based on queue depth."""
    from cast2md.services.runpod_service import get_runpod_service

    service = get_runpod_service()

    # Skip if not available or auto-scale disabled
    if not service.is_available() or not service.settings.runpod_auto_scale:
        return

    # Get transcription queue depth
    with get_db() as conn:
        job_repo = JobRepository(conn)
        counts = job_repo.count_by_status(JobType.TRANSCRIBE)
        queue_depth = counts.get("queued", 0)

    # Check if we should scale
    pods_to_start = service.should_auto_scale(queue_depth)

    if pods_to_start > 0:
        logger.info(f"Auto-scale: queue depth {queue_depth}, starting {pods_to_start} pod(s)")
        for _ in range(pods_to_start):
            try:
                instance_id = service.create_pod_async()
                logger.info(f"Auto-scale: started pod {instance_id}")
            except RuntimeError as e:
                logger.warning(f"Auto-scale: failed to start pod: {e}")
                break  # Stop trying if we hit limits


def retry_pending_transcripts():
    """Retry transcript downloads for episodes that are due.

    Runs hourly to check for:
    1. Episodes with status=awaiting_transcript and next_transcript_retry_at <= now
       - If episode age >= transcript_retry_days: mark as needs_audio (aged out)
       - Else: queue TRANSCRIPT_DOWNLOAD job for retry

    This handles the case where Pocket Casts returns 403 for new episodes
    (transcripts not yet generated) - we retry daily for up to transcript_retry_days.
    """
    from cast2md.config.settings import get_settings

    logger.info("Starting scheduled transcript retry check")
    settings = get_settings()

    now = datetime.now()
    retry_cutoff = now - timedelta(days=settings.transcript_retry_days)

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        job_repo = JobRepository(conn)

        # Get episodes due for retry
        episodes = episode_repo.get_episodes_for_transcript_retry()
        logger.info(f"Found {len(episodes)} episodes due for transcript retry")

        aged_out = 0
        queued = 0
        skipped = 0

        for episode in episodes:
            # Check if episode has aged out (>= transcript_retry_days old)
            if episode.published_at and episode.published_at < retry_cutoff:
                # Transition to needs_audio
                episode_repo.update_transcript_check(
                    episode.id,
                    status=EpisodeStatus.NEEDS_AUDIO,
                    checked_at=now,
                    next_retry_at=None,
                    failure_reason=episode.transcript_failure_reason,
                )
                aged_out += 1
                logger.debug(f"Episode '{episode.title}' aged out, marking as needs_audio")
                continue

            # Check if already has pending job
            if job_repo.has_pending_job(episode.id, JobType.TRANSCRIPT_DOWNLOAD):
                skipped += 1
                continue

            # Queue transcript download job
            job_repo.create(
                episode_id=episode.id,
                job_type=JobType.TRANSCRIPT_DOWNLOAD,
                priority=5,  # Medium priority for retries
            )
            queued += 1
            logger.debug(f"Queued transcript retry for episode: {episode.title}")

    logger.info(
        f"Transcript retry check complete. Queued: {queued}, aged out: {aged_out}, skipped: {skipped}"
    )


def start_scheduler(interval_minutes: int = 60):
    """Start the background scheduler.

    Args:
        interval_minutes: How often to poll feeds (default 60 minutes).
    """
    global scheduler

    if scheduler is not None:
        logger.warning("Scheduler already running")
        return

    scheduler = BackgroundScheduler()

    # Add feed polling job
    scheduler.add_job(
        poll_all_feeds,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="poll_feeds",
        name="Poll all feeds for new episodes",
        replace_existing=True,
        next_run_time=datetime.now(),  # Run immediately on start
    )

    # Add transcript retry job (runs hourly)
    scheduler.add_job(
        retry_pending_transcripts,
        trigger=IntervalTrigger(hours=1),
        id="retry_pending_transcripts",
        name="Retry pending transcript downloads",
        replace_existing=True,
        # Don't run immediately - let feed polling complete first
        next_run_time=datetime.now() + timedelta(minutes=5),
    )

    # Add auto-scale check (runs every minute)
    scheduler.add_job(
        check_auto_scale,
        trigger=IntervalTrigger(minutes=1),
        id="check_auto_scale",
        name="Check RunPod auto-scale",
        replace_existing=True,
        next_run_time=datetime.now() + timedelta(seconds=30),
    )

    scheduler.start()
    logger.info(f"Scheduler started. Feed polling interval: {interval_minutes} minutes, transcript retry: hourly")


def stop_scheduler():
    """Stop the background scheduler."""
    global scheduler

    if scheduler is not None:
        scheduler.shutdown(wait=False)
        scheduler = None
        logger.info("Scheduler stopped")


def get_scheduler_status() -> dict:
    """Get scheduler status info."""
    if scheduler is None:
        return {"running": False}

    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        })

    return {
        "running": scheduler.running,
        "jobs": jobs,
    }
