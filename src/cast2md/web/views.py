"""Web UI views."""

import re

import bleach
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

# Allowed HTML tags for shownotes
ALLOWED_TAGS = ["a", "p", "br", "strong", "b", "em", "i", "ul", "ol", "li", "h1", "h2", "h3", "h4"]
ALLOWED_ATTRIBUTES = {"a": ["href", "title", "target"]}


def strip_html(text: str | None) -> str:
    """Strip HTML tags from text."""
    if not text:
        return ""
    # Remove HTML tags
    clean = re.sub(r'<[^>]+>', '', text)
    # Normalize whitespace
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean


def sanitize_html(text: str | None) -> str:
    """Sanitize HTML to allow only safe tags.

    Allows: a, p, br, strong, b, em, i, ul, ol, li, h1-h4
    """
    if not text:
        return ""
    return bleach.clean(
        text,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        strip=True,
    )


def truncate_html(text: str | None, length: int = 300) -> str:
    """Truncate text, stripping HTML first for safe truncation.

    Args:
        text: Text (possibly with HTML) to truncate.
        length: Maximum length.

    Returns:
        Truncated plain text with ellipsis if needed.
    """
    if not text:
        return ""
    # Strip HTML for truncation to avoid broken tags
    plain = strip_html(text)
    if len(plain) <= length:
        return plain
    # Find last space before cutoff
    truncated = plain[:length]
    last_space = truncated.rfind(' ')
    if last_space > length // 2:
        truncated = truncated[:last_space]
    return truncated + "..."


def render_transcript_html(content: str) -> str:
    """Convert transcript markdown to HTML with timestamp IDs.

    Parses transcript segments and renders them as structured HTML
    with clickable timestamps and data attributes for audio sync.

    Args:
        content: Raw transcript markdown content.

    Returns:
        HTML string with structured transcript segments.
    """
    from html import escape

    from cast2md.search.parser import parse_transcript_segments

    segments = parse_transcript_segments(content)
    html_parts = []

    # Extract header (title + language) before first timestamp
    header_match = re.match(r'^(.*?)(?=\*\*\[)', content, re.DOTALL)
    if header_match:
        header = header_match.group(1).strip()
        # Extract title from markdown header
        title_match = re.search(r'^# (.+)$', header, re.MULTILINE)
        if title_match:
            html_parts.append(f'<h3 class="transcript-title">{escape(title_match.group(1))}</h3>')
        # Extract language metadata
        meta_match = re.search(r'^\*(.+)\*$', header, re.MULTILINE)
        if meta_match:
            html_parts.append(f'<p class="transcript-meta">{escape(meta_match.group(1))}</p>')

    for segment in segments:
        ts_int = int(segment.start)
        minutes = ts_int // 60
        seconds = ts_int % 60
        ts_display = f"{minutes:02d}:{seconds:02d}"

        html_parts.append(
            f'<div class="transcript-segment" id="ts-{ts_int}" '
            f'data-start="{segment.start}" data-end="{segment.end}">'
            f'<a href="#ts-{ts_int}" class="transcript-timestamp">[{ts_display}]</a>'
            f'<span class="transcript-text">{escape(segment.text)}</span>'
            f'</div>'
        )

    return '\n'.join(html_parts)


def configure_templates(t: Jinja2Templates):
    """Configure templates instance."""
    global templates
    templates = t
    # Add custom filters
    templates.env.filters["strip_html"] = strip_html
    templates.env.filters["sanitize_html"] = sanitize_html
    templates.env.filters["truncate_html"] = truncate_html
    templates.env.filters["render_transcript"] = render_transcript_html


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
def feed_detail(
    request: Request,
    feed_id: int,
    page: int = 1,
    per_page: int = 25,
    q: str | None = None,
    status: str | None = None,
):
    """Feed detail page - show episodes with search and filtering."""
    # Validate per_page
    valid_per_page = [10, 25, 50, 100]
    if per_page not in valid_per_page:
        per_page = 25

    # Validate page
    if page < 1:
        page = 1

    # Parse status filter
    episode_status = None
    if status:
        try:
            episode_status = EpisodeStatus(status)
        except ValueError:
            pass  # Invalid status, ignore

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

        # Get total count for the feed (unfiltered)
        total_all = episode_repo.count_by_feed(feed_id)

        offset = (page - 1) * per_page

        # Use search_by_feed if there's a query or status filter
        if q or episode_status:
            episodes, total = episode_repo.search_by_feed(
                feed_id,
                query=q,
                status=episode_status,
                limit=per_page,
                offset=offset,
            )
        else:
            episodes = episode_repo.get_by_feed_paginated(
                feed_id, limit=per_page, offset=offset
            )
            total = total_all

        total_pages = max(1, (total + per_page - 1) // per_page)

        # Clamp page to valid range
        if page > total_pages:
            page = total_pages

    return templates.TemplateResponse(
        "feed_detail.html",
        {
            "request": request,
            "feed": feed,
            "episodes": episodes,
            "page": page,
            "per_page": per_page,
            "valid_per_page": valid_per_page,
            "total": total,
            "total_all": total_all,
            "total_pages": total_pages,
            "query": q or "",
            "status_filter": status or "",
            "statuses": [s.value for s in EpisodeStatus],
        },
    )


@router.get("/episodes/{episode_id}", response_class=HTMLResponse)
def episode_detail(
    request: Request,
    episode_id: int,
    q: str | None = None,
    status: str | None = None,
    per_page: int = 25,
    page: int = 1,
):
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
            "back_query": q,
            "back_status": status,
            "back_per_page": per_page,
            "back_page": page,
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

        # Filter out episodes that have running transcription jobs from "downloaded" list
        running_transcribe_episode_ids = {job.episode_id for job in running_transcriptions}
        queued_transcribe_episode_ids = {job.episode_id for job in queued_transcriptions}
        downloaded = [
            ep for ep in downloaded
            if ep.id not in running_transcribe_episode_ids
            and ep.id not in queued_transcribe_episode_ids
        ]

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


@router.get("/queue", response_class=HTMLResponse)
def queue_management(request: Request, status: str | None = None):
    """Queue management page for viewing and managing all jobs."""
    from datetime import datetime, timedelta

    from cast2md.config.settings import get_settings
    from cast2md.db.models import JobStatus

    stuck_threshold_hours = get_settings().stuck_threshold_hours

    with get_db() as conn:
        job_repo = JobRepository(conn)
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)

        # Get job counts
        job_counts = job_repo.count_by_status()
        stuck_count = job_repo.count_stuck_jobs(stuck_threshold_hours)

        # Get jobs based on filter
        if status == "stuck":
            jobs = job_repo.get_stuck_jobs(stuck_threshold_hours)
        elif status:
            try:
                job_status = JobStatus(status)
                jobs = job_repo.get_all_jobs(status=job_status, limit=100)
            except ValueError:
                jobs = job_repo.get_all_jobs(limit=100)
        else:
            jobs = job_repo.get_all_jobs(limit=100)

        # Build job info with episode and feed details
        stuck_threshold = datetime.utcnow() - timedelta(hours=stuck_threshold_hours)
        job_list = []
        for job in jobs:
            episode = episode_repo.get_by_id(job.episode_id)
            if not episode:
                continue
            feed = feed_repo.get_by_id(episode.feed_id)

            # Calculate runtime
            runtime_seconds = None
            is_stuck = False
            if job.status == JobStatus.RUNNING and job.started_at:
                runtime_seconds = int((datetime.utcnow() - job.started_at).total_seconds())
                is_stuck = job.started_at < stuck_threshold

            job_list.append({
                "job": job,
                "episode": episode,
                "feed": feed,
                "is_stuck": is_stuck,
                "runtime_seconds": runtime_seconds,
            })

    return templates.TemplateResponse(
        "queue.html",
        {
            "request": request,
            "jobs": job_list,
            "job_counts": job_counts,
            "stuck_count": stuck_count,
            "current_filter": status or "all",
            "stuck_threshold_hours": stuck_threshold_hours,
        },
    )


@router.get("/search", response_class=HTMLResponse)
def transcript_search_page(
    request: Request,
    q: str | None = None,
    feed_id: str | None = None,
    page: int = 1,
    per_page: int = 20,
    type: str = "transcripts",
):
    """Unified search page for episodes and transcripts."""
    from cast2md.search.repository import TranscriptSearchRepository

    # Validate search type
    valid_types = ("episodes", "transcripts", "everything")
    search_type = type if type in valid_types else "transcripts"

    # Convert feed_id to int or None (handles empty string from form)
    feed_id_int: int | None = None
    if feed_id and feed_id.strip():
        try:
            feed_id_int = int(feed_id)
        except ValueError:
            pass

    transcript_results = []
    episode_results = []
    transcript_total = 0
    episode_total = 0
    total_pages = 1
    index_stats = {"total_segments": 0, "indexed_episodes": 0}
    feeds = []

    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)
        search_repo = TranscriptSearchRepository(conn)

        # Get all feeds for dropdown
        feeds = feed_repo.get_all()

        # Get index stats
        index_stats = {
            "total_segments": search_repo.get_indexed_count(),
            "indexed_episodes": len(search_repo.get_indexed_episodes()),
        }

        # Perform search if query provided
        if q:
            offset = (page - 1) * per_page

            # Episode search (for "episodes" or "everything" modes)
            if search_type in ("episodes", "everything"):
                episode_results, episode_total = episode_repo.search_episodes_fts_full(
                    query=q,
                    feed_id=feed_id_int,
                    limit=per_page,
                    offset=offset,
                )
                # Add feed info to episodes for display
                episode_results_with_feed = []
                for ep in episode_results:
                    feed = feed_repo.get_by_id(ep.feed_id)
                    episode_results_with_feed.append({
                        "episode": ep,
                        "feed": feed,
                    })
                episode_results = episode_results_with_feed

            # Transcript search (for "transcripts" or "everything" modes)
            if search_type in ("transcripts", "everything"):
                response = search_repo.search(
                    query=q,
                    feed_id=feed_id_int,
                    limit=per_page,
                    offset=offset,
                )
                transcript_results = response.results
                transcript_total = response.total

            # Calculate total pages based on search type
            if search_type == "episodes":
                total_pages = max(1, (episode_total + per_page - 1) // per_page)
            elif search_type == "transcripts":
                total_pages = max(1, (transcript_total + per_page - 1) // per_page)
            else:  # everything - use max of both
                max_total = max(episode_total, transcript_total)
                total_pages = max(1, (max_total + per_page - 1) // per_page)

    return templates.TemplateResponse(
        "search.html",
        {
            "request": request,
            "query": q or "",
            "feed_id": feed_id_int,
            "feeds": feeds,
            "search_type": search_type,
            "episode_results": episode_results,
            "transcript_results": transcript_results,
            "episode_total": episode_total,
            "transcript_total": transcript_total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "index_stats": index_stats,
        },
    )
