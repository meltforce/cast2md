"""Background scheduler for feed polling."""

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from cast2md.db.connection import get_db
from cast2md.db.repository import FeedRepository
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

    scheduler.start()
    logger.info(f"Scheduler started. Polling interval: {interval_minutes} minutes")


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
