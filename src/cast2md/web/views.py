"""Web UI views."""

import re

import bleach
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from cast2md.db.connection import get_db
from cast2md.db.models import EpisodeStatus, JobType, NodeStatus
from cast2md.db.repository import (
    EpisodeRepository,
    FeedRepository,
    JobRepository,
    TranscriberNodeRepository,
)
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
    Falls back to plain text rendering for transcripts without timestamps.

    Args:
        content: Raw transcript markdown content.

    Returns:
        HTML string with structured transcript segments.
    """
    from html import escape

    from cast2md.search.parser import merge_word_level_segments, parse_transcript_segments

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

    # Merge word-level segments into phrases for better readability
    segments = merge_word_level_segments(segments)

    if segments:
        # Render with timestamps
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
    else:
        # Fallback: render plain text for transcripts without timestamps
        # Extract title and metadata first
        title_match = re.search(r'^# (.+)$', content, re.MULTILINE)
        if title_match:
            html_parts.append(f'<h3 class="transcript-title">{escape(title_match.group(1))}</h3>')
        meta_match = re.search(r'^\*(.+)\*$', content, re.MULTILINE)
        if meta_match:
            html_parts.append(f'<p class="transcript-meta">{escape(meta_match.group(1))}</p>')

        # Get the body text (skip header lines)
        lines = content.split('\n')
        body_lines = []
        skip_header = True
        for line in lines:
            if skip_header:
                # Skip title and metadata lines
                if line.startswith('#') or (line.startswith('*') and line.endswith('*')):
                    continue
                if line.strip() == '':
                    continue
                skip_header = False
            body_lines.append(line)

        # Render paragraphs
        body_text = '\n'.join(body_lines)
        paragraphs = body_text.split('\n\n')
        for para in paragraphs:
            para = para.strip()
            if para:
                html_parts.append(f'<p class="transcript-text">{escape(para)}</p>')

    return '\n'.join(html_parts)


def sanitize_search_snippet(text: str | None) -> str:
    """Sanitize search snippet, keeping only <mark> tags for highlighting.

    PostgreSQL ts_headline adds <mark> tags for keyword highlighting.
    This strips all other HTML but preserves <mark> for rendering.
    """
    if not text:
        return ""
    from html import escape
    # First escape everything
    escaped = escape(text)
    # Then restore <mark> and </mark> tags
    escaped = escaped.replace('&lt;mark&gt;', '<mark>')
    escaped = escaped.replace('&lt;/mark&gt;', '</mark>')
    return escaped


def timeago(dt) -> str:
    """Convert datetime to human-readable relative time.

    Args:
        dt: datetime object or ISO string.

    Returns:
        Relative time string like "2h ago", "3d ago", "Jan 15".
    """
    from datetime import datetime

    if dt is None:
        return ""

    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return dt[:10] if len(dt) >= 10 else dt

    now = datetime.now()
    if dt.tzinfo:
        # Make now timezone-aware if dt is
        now = datetime.now(dt.tzinfo)

    diff = now - dt
    seconds = diff.total_seconds()

    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes}m ago"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours}h ago"
    elif seconds < 604800:  # 7 days
        days = int(seconds / 86400)
        return f"{days}d ago"
    else:
        # Show date for older items
        return dt.strftime("%b %d")


def configure_templates(t: Jinja2Templates):
    """Configure templates instance."""
    global templates
    templates = t
    # Add custom filters
    templates.env.filters["strip_html"] = strip_html
    templates.env.filters["sanitize_html"] = sanitize_html
    templates.env.filters["truncate_html"] = truncate_html
    templates.env.filters["render_transcript"] = render_transcript_html
    templates.env.filters["search_snippet"] = sanitize_search_snippet
    templates.env.filters["timeago"] = timeago


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    """Home page - redirect to search."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/search", status_code=302)


@router.get("/feeds", response_class=HTMLResponse)
def feeds_index(request: Request):
    """Feeds page - list all feeds."""
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
        job_repo = JobRepository(conn)

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

        # Get transcript source stats for this feed
        transcript_stats = episode_repo.get_transcript_source_stats(feed_id)

        # Count episodes needing transcription (new or needs_audio)
        pending_count = episode_repo.count_by_feed_and_status(feed_id, EpisodeStatus.NEW)
        unavailable_count = episode_repo.count_by_feed_and_status(feed_id, EpisodeStatus.NEEDS_AUDIO)
        needs_transcription_count = pending_count + unavailable_count

        # Get set of episode IDs that have pending/running jobs (for "queued" display)
        episode_ids = [ep.id for ep in episodes]
        queued_episode_ids = set()
        for ep_id in episode_ids:
            if job_repo.has_pending_job(ep_id, JobType.TRANSCRIPT_DOWNLOAD):
                queued_episode_ids.add(ep_id)
            elif job_repo.has_pending_job(ep_id, JobType.DOWNLOAD):
                queued_episode_ids.add(ep_id)

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
            "transcript_stats": transcript_stats,
            "queued_episode_ids": queued_episode_ids,
            "needs_transcription_count": needs_transcription_count,
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
    from cast2md.config.settings import get_settings

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

    # Get current model for retranscribe comparison
    settings = get_settings()
    current_model = settings.whisper_model

    return templates.TemplateResponse(
        "episode_detail.html",
        {
            "request": request,
            "episode": episode,
            "feed": feed,
            "transcript_content": transcript_content,
            "current_model": current_model,
            "back_query": q,
            "back_status": status,
            "back_per_page": per_page,
            "back_page": page,
        },
    )


@router.get("/status", response_class=HTMLResponse)
def status_page_redirect(request: Request):
    """Redirect old status URL to admin."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/admin", status_code=302)


@router.get("/admin", response_class=HTMLResponse)
def admin_status_page(request: Request):
    """Admin status page - high-level dashboard with worker cards."""
    from cast2md.search.repository import TranscriptSearchRepository

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)
        job_repo = JobRepository(conn)
        node_repo = TranscriberNodeRepository(conn)
        search_repo = TranscriptSearchRepository(conn)

        status_counts = episode_repo.count_by_status()

        # Search index stats
        search_stats = {
            "indexed_episodes": len(search_repo.get_indexed_episodes()),
            "embedded_episodes": len(search_repo.get_embedded_episodes()),
        }
        feeds = feed_repo.get_all()

        # Get running jobs for worker status display
        running_downloads = job_repo.get_running_jobs(JobType.DOWNLOAD)
        running_transcriptions = job_repo.get_running_jobs(JobType.TRANSCRIBE)
        running_transcript_downloads = job_repo.get_running_jobs(JobType.TRANSCRIPT_DOWNLOAD)

        # Get episode info for running jobs
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

        running_transcript_download_episodes = []
        for job in running_transcript_downloads:
            ep = episode_repo.get_by_id(job.episode_id)
            if ep:
                running_transcript_download_episodes.append({"job": job, "episode": ep})

        # Get all remote nodes
        nodes = node_repo.get_all()

    # Get worker status from manager
    worker_manager = get_worker_manager()
    queue_status = worker_manager.get_status()

    # Build worker groups for card display
    # Track assigned jobs to detect orphans
    assigned_download_job_ids = set()
    assigned_transcribe_job_ids = set()
    assigned_transcript_download_job_ids = set()

    # Audio Download card
    download_workers = []
    download_job_index = 0
    for i in range(queue_status["download_workers"]):
        job = None
        episode = None
        if download_job_index < len(running_download_episodes):
            item = running_download_episodes[download_job_index]
            job = item["job"]
            episode = item["episode"]
            assigned_download_job_ids.add(job.id)
            download_job_index += 1

        download_workers.append({
            "status": "busy" if job else "idle",
            "job": job,
            "episode": episode,
        })

    # Check for orphaned download jobs
    orphaned_downloads = []
    for item in running_download_episodes:
        if item["job"].id not in assigned_download_job_ids:
            orphaned_downloads.append({
                "status": "stuck",
                "job": item["job"],
                "episode": item["episode"],
            })

    # Transcript Fetch card
    active_tdl_count = len(running_transcript_download_episodes)
    total_tdl_workers = queue_status["transcript_download_workers"]
    for item in running_transcript_download_episodes:
        assigned_transcript_download_job_ids.add(item["job"].id)

    # Check for orphaned transcript download jobs
    orphaned_transcript_downloads = []
    for item in running_transcript_download_episodes:
        if item["job"].id not in assigned_transcript_download_job_ids:
            orphaned_transcript_downloads.append({
                "status": "stuck",
                "job": item["job"],
                "episode": item["episode"],
            })

    # Transcription card - server worker
    server_worker = {"status": "idle", "job": None, "episode": None, "progress": None}
    for item in running_transcribe_episodes:
        node_id = item["job"].assigned_node_id
        if not node_id or node_id == "local":
            server_worker = {
                "status": "busy",
                "job": item["job"],
                "episode": item["episode"],
                "progress": item["job"].progress_percent,
            }
            assigned_transcribe_job_ids.add(item["job"].id)
            break

    # Transcription card - remote nodes
    remote_nodes = []
    if queue_status.get("distributed_enabled"):
        for node in nodes:
            node_job = None
            node_episode = None
            for item in running_transcribe_episodes:
                if item["job"].assigned_node_id == node.id:
                    node_job = item["job"]
                    node_episode = item["episode"]
                    assigned_transcribe_job_ids.add(node_job.id)
                    break

            is_mlx = node.whisper_backend in ("mlx", "auto")
            remote_nodes.append({
                "name": node.name,
                "status": node.status.value,
                "job": node_job,
                "episode": node_episode,
                "progress": None if is_mlx else (node_job.progress_percent if node_job else None),
            })

    # Check for orphaned transcription jobs
    orphaned_transcriptions = []
    for item in running_transcribe_episodes:
        if item["job"].id not in assigned_transcribe_job_ids:
            orphaned_transcriptions.append({
                "status": "stuck",
                "job": item["job"],
                "episode": item["episode"],
                "progress": item["job"].progress_percent,
            })

    # Build worker_groups structure for template
    worker_groups = {
        "download": {
            "title": "Audio Download",
            "workers": download_workers + orphaned_downloads,
            "queued": queue_status["download_queue"]["queued"],
        },
        "transcript_fetch": {
            "title": "Transcript Fetch",
            "active_count": active_tdl_count,
            "total_count": total_tdl_workers,
            "queued": queue_status["transcript_download_queue"]["queued"],
            "orphaned": orphaned_transcript_downloads,
        },
        "transcription": {
            "title": "Transcription",
            "server": server_worker,
            "nodes": remote_nodes,
            "orphaned": orphaned_transcriptions,
            "queued": queue_status["transcribe_queue"]["queued"],
            "distributed_enabled": queue_status.get("distributed_enabled", False),
        },
    }

    return templates.TemplateResponse(
        "status.html",
        {
            "request": request,
            "status_counts": status_counts,
            "feed_count": len(feeds),
            "queue_status": queue_status,
            "worker_groups": worker_groups,
            "search_stats": search_stats,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page_redirect(request: Request):
    """Redirect old settings URL to admin."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/admin/settings", status_code=302)


@router.get("/admin/settings", response_class=HTMLResponse)
def admin_settings_page(request: Request):
    """Admin settings page."""
    return templates.TemplateResponse(
        "settings.html",
        {"request": request},
    )


@router.get("/queue", response_class=HTMLResponse)
def queue_page_redirect(request: Request, status: str | None = None):
    """Redirect old queue URL to admin."""
    from fastapi.responses import RedirectResponse
    url = "/admin/queue"
    if status:
        url += f"?status={status}"
    return RedirectResponse(url=url, status_code=302)


@router.get("/admin/queue", response_class=HTMLResponse)
def admin_queue_page(request: Request, status: str | None = None):
    """Admin queue management page for viewing and managing all jobs."""
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
        stuck_threshold = datetime.now() - timedelta(hours=stuck_threshold_hours)
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
                runtime_seconds = int((datetime.now() - job.started_at).total_seconds())
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
    mode: str = "hybrid",
    page: int = 1,
    per_page: int = 20,
):
    """Unified search page for episodes and transcripts.

    Supports three search modes:
    - hybrid: Combines keyword and semantic search using RRF
    - keyword: Traditional FTS5 full-text search
    - semantic: Vector similarity search for conceptual matching
    """
    from cast2md.search.repository import TranscriptSearchRepository

    # Validate mode
    valid_modes = ("hybrid", "keyword", "semantic")
    if mode not in valid_modes:
        mode = "hybrid"

    # Convert feed_id to int or None (handles empty string from form)
    feed_id_int: int | None = None
    if feed_id and feed_id.strip():
        try:
            feed_id_int = int(feed_id)
        except ValueError:
            pass

    results = []
    total = 0
    total_pages = 1
    index_stats = {"total_segments": 0, "indexed_episodes": 0, "embedded_episodes": 0, "total_embeddings": 0}
    feeds = []
    actual_mode = mode
    recent_transcripts = []

    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)
        search_repo = TranscriptSearchRepository(conn)

        # Get all feeds for dropdown
        feeds = feed_repo.get_all()

        # Get index stats including semantic search stats
        index_stats = {
            "total_segments": search_repo.get_indexed_count(),
            "indexed_episodes": len(search_repo.get_indexed_episodes()),
            "embedded_episodes": len(search_repo.get_embedded_episodes()),
            "total_embeddings": search_repo.get_embedding_count(),
        }

        # Perform hybrid search if query provided
        if q:
            offset = (page - 1) * per_page
            response = search_repo.hybrid_search(
                query=q,
                feed_id=feed_id_int,
                limit=per_page,
                offset=offset,
                mode=mode,  # type: ignore[arg-type]
            )
            results = response.results
            total = response.total
            actual_mode = response.mode
            total_pages = max(1, (total + per_page - 1) // per_page)
        else:
            # No query - show recent transcripts for empty state
            # Fetch enough cards for horizontal scroll on large screens
            recent_transcripts = episode_repo.get_recent_transcribed_episodes(limit=10)

    return templates.TemplateResponse(
        "search.html",
        {
            "request": request,
            "query": q or "",
            "feed_id": feed_id_int,
            "feeds": feeds,
            "results": results,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "index_stats": index_stats,
            "mode": mode,
            "actual_mode": actual_mode,
            "recent_transcripts": recent_transcripts,
        },
    )
