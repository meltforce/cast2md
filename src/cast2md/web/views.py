"""Web UI views."""

import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from cast2md.db.connection import get_db
from cast2md.db.models import EpisodeStatus, JobType
from cast2md.db.repository import EpisodeRepository, FeedRepository, JobRepository
from cast2md.worker import get_worker_manager

router = APIRouter(tags=["web"])

# Templates will be configured in main.py
templates: Jinja2Templates = None


def strip_html(text: str | None) -> str:
    """Strip HTML tags from text."""
    if not text:
        return ""
    # Remove HTML tags
    clean = re.sub(r'<[^>]+>', '', text)
    # Normalize whitespace
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean


def configure_templates(t: Jinja2Templates):
    """Configure templates instance."""
    global templates
    templates = t
    # Add custom filter
    templates.env.filters["strip_html"] = strip_html


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Home page - list all feeds."""
    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)

        feeds = feed_repo.get_all()
        status_counts = episode_repo.count_by_status()

        # Add episode counts to feeds
        feeds_with_counts = []
        for feed in feeds:
            feeds_with_counts.append({
                "feed": feed,
                "episode_count": episode_repo.count_by_feed(feed.id),
            })

    total_episodes = sum(status_counts.values())

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "feeds": feeds_with_counts,
            "status_counts": status_counts,
            "total_episodes": total_episodes,
        },
    )


@router.get("/feeds/{feed_id}", response_class=HTMLResponse)
def feed_detail(request: Request, feed_id: int, page: int = 1, per_page: int = 20):
    """Feed detail page - show episodes."""
    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)

        feed = feed_repo.get_by_id(feed_id)
        if not feed:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": "Feed not found"},
                status_code=404,
            )

        # Get paginated episodes
        total = episode_repo.count_by_feed(feed_id)
        offset = (page - 1) * per_page
        episodes = episode_repo.get_by_feed(feed_id, limit=per_page + offset)
        episodes = episodes[offset : offset + per_page]

        total_pages = (total + per_page - 1) // per_page

    return templates.TemplateResponse(
        "feed_detail.html",
        {
            "request": request,
            "feed": feed,
            "episodes": episodes,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
        },
    )


@router.get("/episodes/{episode_id}", response_class=HTMLResponse)
def episode_detail(request: Request, episode_id: int):
    """Episode detail page."""
    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)

        episode = episode_repo.get_by_id(episode_id)
        if not episode:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": "Episode not found"},
                status_code=404,
            )

        feed = feed_repo.get_by_id(episode.feed_id)

    # Read transcript content if available
    transcript_content = None
    if episode.transcript_path:
        try:
            from pathlib import Path
            transcript_content = Path(episode.transcript_path).read_text()
        except Exception:
            pass

    return templates.TemplateResponse(
        "episode_detail.html",
        {
            "request": request,
            "episode": episode,
            "feed": feed,
            "transcript_content": transcript_content,
        },
    )


@router.get("/status", response_class=HTMLResponse)
def status_page(request: Request):
    """Status page showing episodes by status."""
    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)
        job_repo = JobRepository(conn)

        status_counts = episode_repo.count_by_status()
        feeds = feed_repo.get_all()

        # Get recent episodes for each active status
        pending = episode_repo.get_by_status(EpisodeStatus.PENDING, limit=10)
        downloading = episode_repo.get_by_status(EpisodeStatus.DOWNLOADING, limit=10)
        downloaded = episode_repo.get_by_status(EpisodeStatus.DOWNLOADED, limit=10)
        transcribing = episode_repo.get_by_status(EpisodeStatus.TRANSCRIBING, limit=10)
        failed = episode_repo.get_by_status(EpisodeStatus.FAILED, limit=10)

        # Get queued jobs
        queued_downloads = job_repo.get_queued_jobs(JobType.DOWNLOAD, limit=10)
        queued_transcriptions = job_repo.get_queued_jobs(JobType.TRANSCRIBE, limit=10)
        running_downloads = job_repo.get_running_jobs(JobType.DOWNLOAD)
        running_transcriptions = job_repo.get_running_jobs(JobType.TRANSCRIBE)

        # Get episode info for queued jobs
        queued_download_episodes = []
        for job in queued_downloads:
            ep = episode_repo.get_by_id(job.episode_id)
            if ep:
                queued_download_episodes.append({"job": job, "episode": ep})

        queued_transcribe_episodes = []
        for job in queued_transcriptions:
            ep = episode_repo.get_by_id(job.episode_id)
            if ep:
                queued_transcribe_episodes.append({"job": job, "episode": ep})

        running_download_episodes = []
        for job in running_downloads:
            ep = episode_repo.get_by_id(job.episode_id)
            if ep:
                running_download_episodes.append({"job": job, "episode": ep})

        running_transcribe_episodes = []
        for job in running_transcriptions:
            ep = episode_repo.get_by_id(job.episode_id)
            if ep:
                running_transcribe_episodes.append({"job": job, "episode": ep})

    # Get worker status
    worker_manager = get_worker_manager()
    queue_status = worker_manager.get_status()

    return templates.TemplateResponse(
        "status.html",
        {
            "request": request,
            "status_counts": status_counts,
            "feed_count": len(feeds),
            "pending": pending,
            "downloading": downloading,
            "downloaded": downloaded,
            "transcribing": transcribing,
            "failed": failed,
            "queue_status": queue_status,
            "queued_downloads": queued_download_episodes,
            "queued_transcriptions": queued_transcribe_episodes,
            "running_downloads": running_download_episodes,
            "running_transcriptions": running_transcribe_episodes,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    """Settings page."""
    return templates.TemplateResponse(
        "settings.html",
        {"request": request},
    )
