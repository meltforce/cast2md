"""Web UI views."""

import re

import bleach
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from cast2md.db.connection import get_db
from cast2md.db.models import EpisodeStatus, JobType
from cast2md.db.repository import (
    EpisodeRepository,
    FeedRepository,
    JobRepository,
)
from cast2md.web.status_view import build_status_context, collect_status_data
from cast2md.worker import get_worker_manager

router = APIRouter(tags=["web"])

# Templates will be configured in main.py
templates: Jinja2Templates = None


def _get_raw_version() -> str:
    """Get version string preserving the original format (e.g., '2026.01').

    Python's importlib.metadata normalizes '2026.01' to '2026.1' per PEP 440,
    but git tags use the zero-padded format. Re-pad the month component.
    """
    import cast2md

    version = cast2md.__version__
    # Re-pad: "2026.1" -> "2026.01", "2026.12" stays "2026.12"
    parts = version.split(".")
    if len(parts) >= 2 and len(parts[1]) == 1:
        parts[1] = parts[1].zfill(2)
    return ".".join(parts)


# Allowed HTML tags for shownotes
ALLOWED_TAGS = ["a", "p", "br", "strong", "b", "em", "i", "ul", "ol", "li", "h1", "h2", "h3", "h4"]
ALLOWED_ATTRIBUTES = {"a": ["href", "title", "target"]}


def strip_html(text: str | None) -> str:
    """Strip HTML tags from text."""
    if not text:
        return ""
    # Remove HTML tags
    clean = re.sub(r"<[^>]+>", "", text)
    # Normalize whitespace
    clean = re.sub(r"\s+", " ", clean).strip()
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
    last_space = truncated.rfind(" ")
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
    header_match = re.match(r"^(.*?)(?=\*\*\[)", content, re.DOTALL)
    if header_match:
        header = header_match.group(1).strip()
        # Extract title from markdown header
        title_match = re.search(r"^# (.+)$", header, re.MULTILINE)
        if title_match:
            html_parts.append(f'<h3 class="transcript-title">{escape(title_match.group(1))}</h3>')
        # Extract language metadata
        meta_match = re.search(r"^\*(.+)\*$", header, re.MULTILINE)
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
                f"</div>"
            )
    else:
        # Fallback: render plain text for transcripts without timestamps
        # Extract title and metadata first
        title_match = re.search(r"^# (.+)$", content, re.MULTILINE)
        if title_match:
            html_parts.append(f'<h3 class="transcript-title">{escape(title_match.group(1))}</h3>')
        meta_match = re.search(r"^\*(.+)\*$", content, re.MULTILINE)
        if meta_match:
            html_parts.append(f'<p class="transcript-meta">{escape(meta_match.group(1))}</p>')

        # Get the body text (skip header lines)
        lines = content.split("\n")
        body_lines = []
        skip_header = True
        for line in lines:
            if skip_header:
                # Skip title and metadata lines
                if line.startswith("#") or (line.startswith("*") and line.endswith("*")):
                    continue
                if line.strip() == "":
                    continue
                skip_header = False
            body_lines.append(line)

        # Render paragraphs
        body_text = "\n".join(body_lines)
        paragraphs = body_text.split("\n\n")
        for para in paragraphs:
            para = para.strip()
            if para:
                html_parts.append(f'<p class="transcript-text">{escape(para)}</p>')

    return "\n".join(html_parts)


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
    escaped = escaped.replace("&lt;mark&gt;", "<mark>")
    escaped = escaped.replace("&lt;/mark&gt;", "</mark>")
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


def format_duration(seconds: int | None) -> str:
    """Format an episode duration compactly for media rows."""
    if not seconds:
        return ""
    hours, remainder = divmod(int(seconds), 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def episode_excerpt(text: str | None, length: int = 240) -> str:
    """Create a clean, compact description excerpt for episode lists."""
    plain = strip_html(text)
    boilerplate = r"^(show notes|episode notes|in this episode|on this episode)\s*[:—-]\s*"
    plain = re.sub(boilerplate, "", plain, flags=re.IGNORECASE)
    if len(plain) <= length:
        return plain
    shortened = plain[:length].rsplit(" ", 1)[0]
    return f"{shortened}…"


def episode_day_group(dt) -> str:
    """Return the stream group label for an episode publication date."""
    from datetime import datetime, timedelta

    if not dt:
        return "Undated"
    today = datetime.now(dt.tzinfo).date() if dt.tzinfo else datetime.now().date()
    if dt.date() == today:
        return "Today"
    if dt.date() == today - timedelta(days=1):
        return "Yesterday"
    return f"{dt.day} {dt.strftime('%B')}"


def highlight_query(text: str | None, query: str) -> str:
    """Escape a search excerpt and emphasize matching query terms."""
    from html import escape

    clean = escape(strip_html(text))
    terms = sorted(set(re.findall(r"[\w'-]+", query)), key=len, reverse=True)
    if not terms:
        return clean
    pattern = re.compile("(" + "|".join(re.escape(term) for term in terms) + ")", re.I)
    return pattern.sub(r"<strong>\1</strong>", clean)


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
    templates.env.filters["duration"] = format_duration
    templates.env.filters["episode_excerpt"] = episode_excerpt
    templates.env.filters["highlight_query"] = highlight_query
    # Add global template variables - read version from pyproject.toml
    # to preserve the original format (e.g., "2026.01" not Python-normalized "2026.1")
    templates.env.globals["app_version"] = _get_raw_version()


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    """Home page - redirect to the episode library."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/episodes", status_code=302)


@router.get("/episodes", response_class=HTMLResponse)
def episodes_index(
    request: Request,
    q: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 20,
):
    """Cross-feed episode library, newest first."""
    valid_statuses = {"completed", "queued", "failed"}
    status_group = status if status in valid_statuses else None
    page = max(1, page)
    per_page = per_page if per_page in (20, 40, 80) else 20

    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)
        job_repo = JobRepository(conn)
        feeds = feed_repo.get_all()
        status_counts = episode_repo.count_by_status()
        raw_items, total = episode_repo.get_library_page(
            query=q,
            status_group=status_group,
            limit=per_page,
            offset=(page - 1) * per_page,
        )
        total_pages = max(1, (total + per_page - 1) // per_page)
        if page > total_pages:
            page = total_pages
            raw_items, total = episode_repo.get_library_page(
                query=q,
                status_group=status_group,
                limit=per_page,
                offset=(page - 1) * per_page,
            )

        items = []
        for episode, feed_title, feed_image_url in raw_items:
            progress = None
            worker = None
            if episode.status == EpisodeStatus.TRANSCRIBING:
                running_jobs = [
                    job for job in job_repo.get_by_episode(episode.id) if job.started_at
                ]
                if running_jobs:
                    progress = running_jobs[-1].progress_percent
                    worker = running_jobs[-1].assigned_node_id or "local worker"
            items.append(
                {
                    "episode": episode,
                    "feed_title": feed_title,
                    "feed_image_url": feed_image_url,
                    "summary": episode_excerpt(episode.description),
                    "progress": progress,
                    "worker": worker,
                    "group_label": episode_day_group(episode.published_at),
                }
            )

    last_polled = max((feed.last_polled for feed in feeds if feed.last_polled), default=None)
    return templates.TemplateResponse(
        "episodes.html",
        {
            "request": request,
            "items": items,
            "query": q or "",
            "status_filter": status_group or "all",
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "feed_count": len(feeds),
            "episode_count": sum(status_counts.values()),
            "transcribed_count": status_counts.get(EpisodeStatus.COMPLETED.value, 0),
            "transcribing_count": status_counts.get(EpisodeStatus.TRANSCRIBING.value, 0),
            "last_polled": last_polled,
        },
    )


@router.get("/feeds", response_class=HTMLResponse)
def feeds_index(request: Request, sort: str = "recent"):
    """Feeds page - list all feeds."""
    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)

        feeds = feed_repo.get_all()
        status_counts = episode_repo.count_by_status()
        per_feed_counts = episode_repo.count_by_status_per_feed()
        latest_published = episode_repo.get_latest_published_at_per_feed()

        # Add episode counts to feeds
        feeds_with_counts = []
        for feed in feeds:
            feeds_with_counts.append(
                {
                    "feed": feed,
                    "episode_count": sum(per_feed_counts.get(feed.id, {}).values()),
                    "status_counts": per_feed_counts.get(feed.id, {}),
                    "latest_published": latest_published.get(feed.id),
                }
            )

        if sort == "name":
            feeds_with_counts.sort(key=lambda item: item["feed"].display_title.casefold())
        elif sort == "episodes":
            feeds_with_counts.sort(
                key=lambda item: (item["episode_count"], item["feed"].display_title.casefold()),
                reverse=True,
            )
        else:
            sort = "recent"
            feeds_with_counts.sort(
                key=lambda item: item["latest_published"] or item["feed"].created_at,
                reverse=True,
            )

    total_episodes = sum(status_counts.values())

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "feeds": feeds_with_counts,
            "status_counts": status_counts,
            "total_episodes": total_episodes,
            "sort": sort,
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

        # Count permanent failures
        permanent_failure_count = episode_repo.count_permanent_failures(feed_id)

        # Exclude permanent failures from default view (no status filter active)
        exclude_pf = not q and not episode_status
        total_all = episode_repo.count_by_feed(feed_id, exclude_permanent_failures=exclude_pf)

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
                feed_id,
                limit=per_page,
                offset=offset,
                exclude_permanent_failures=True,
            )
            total = total_all

        total_pages = max(1, (total + per_page - 1) // per_page)

        # Clamp page to valid range
        if page > total_pages:
            page = total_pages

        # Get transcript source stats for this feed
        transcript_stats = episode_repo.get_transcript_source_stats(feed_id)
        feed_status_counts = episode_repo.get_status_counts_for_feed(feed_id)

        # Count episodes needing transcription (new or needs_audio)
        pending_count = episode_repo.count_by_feed_and_status(feed_id, EpisodeStatus.NEW)
        unavailable_count = episode_repo.count_by_feed_and_status(
            feed_id, EpisodeStatus.NEEDS_AUDIO
        )
        needs_transcription_count = pending_count + unavailable_count

        # Count audio_ready episodes (downloaded but not yet transcribed)
        audio_ready_count = episode_repo.count_by_feed_and_status(
            feed_id, EpisodeStatus.AUDIO_READY
        )

        # Get set of episode IDs that have pending/running jobs (for "queued" display)
        episode_ids = [ep.id for ep in episodes]
        queued_episode_ids = set()
        for ep_id in episode_ids:
            if job_repo.has_pending_job(ep_id, JobType.TRANSCRIPT_DOWNLOAD):
                queued_episode_ids.add(ep_id)
            elif job_repo.has_pending_job(ep_id, JobType.DOWNLOAD):
                queued_episode_ids.add(ep_id)

        feed_items = []
        action_map = {
            EpisodeStatus.NEW: ("download", "Download Audio"),
            EpisodeStatus.AWAITING_TRANSCRIPT: ("download", "Download Audio"),
            EpisodeStatus.NEEDS_AUDIO: ("download", "Download Audio"),
            EpisodeStatus.AUDIO_READY: ("transcribe", "Queue Transcription"),
            EpisodeStatus.FAILED: ("retry", "Retry"),
        }
        for episode in episodes:
            display_status = (
                "queued"
                if episode.id in queued_episode_ids and episode.status == EpisodeStatus.NEW
                else episode.status.value
            )
            action, action_label = action_map.get(episode.status, (None, None))
            if episode.id in queued_episode_ids or episode.permanent_failure:
                action, action_label = None, None
            feed_items.append(
                {
                    "episode": episode,
                    "feed_title": feed.display_title,
                    "feed_image_url": feed.image_url,
                    "summary": episode_excerpt(episode.description),
                    "display_status": display_status,
                    "is_queued": episode.id in queued_episode_ids,
                    "has_external_transcript": bool(
                        episode.transcript_url or episode.pocketcasts_transcript_url
                    ),
                    "checkbox_disabled": (
                        episode.status != EpisodeStatus.NEW or episode.id in queued_episode_ids
                    ),
                    "action": action,
                    "action_label": action_label,
                }
            )

    return templates.TemplateResponse(
        "feed_detail.html",
        {
            "request": request,
            "feed": feed,
            "episodes": episodes,
            "feed_items": feed_items,
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
            "feed_status_counts": feed_status_counts,
            "queued_episode_ids": queued_episode_ids,
            "needs_transcription_count": needs_transcription_count,
            "audio_ready_count": audio_ready_count,
            "permanent_failure_count": permanent_failure_count,
        },
    )


@router.get("/episodes/{episode_id}", response_class=HTMLResponse)
def episode_detail(
    request: Request,
    episode_id: int,
    # Back navigation params
    back: str | None = None,  # "episodes", "search", "feed", or None
    back_url: str | None = None,  # Full URL to return to (for search with query)
    # Legacy feed back params (for backwards compatibility)
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

    # Build back link info
    back_label = f"Back to {feed.display_title}"
    back_href = f"/feeds/{feed.id}"

    if back == "episodes":
        back_label = "Back to Episodes"
        back_href = back_url if back_url else "/episodes"
    elif back == "search":
        back_label = "Back to Search"
        back_href = back_url if back_url else "/search"
    elif back == "feed" or q or status or page != 1:
        # Build feed URL with params
        back_params = []
        if q:
            back_params.append(f"q={q}")
        if status:
            back_params.append(f"status={status}")
        if per_page != 25:
            back_params.append(f"per_page={per_page}")
        if page != 1:
            back_params.append(f"page={page}")
        if back_params:
            back_href = f"/feeds/{feed.id}?{'&'.join(back_params)}"

    return templates.TemplateResponse(
        "episode_detail.html",
        {
            "request": request,
            "episode": episode,
            "feed": feed,
            "transcript_content": transcript_content,
            "current_model": current_model,
            "back_label": back_label,
            "back_href": back_href,
            # Empty unless the operator configured vimmary; the template hides
            # the button in that case.
            "vimmary_url": settings.vimmary_url.rstrip("/"),
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
    with get_db() as conn:
        data = collect_status_data(conn)

    context = build_status_context(data, get_worker_manager().get_status())
    return templates.TemplateResponse("status.html", {"request": request, **context})


@router.get("/settings", response_class=HTMLResponse)
def settings_page_redirect(request: Request):
    """Redirect old settings URL to admin."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/admin/settings", status_code=302)


@router.get("/admin/settings", response_class=HTMLResponse)
def admin_settings_page(request: Request, tab: str = "general"):
    """Admin settings page."""
    valid_tabs = {"general", "transcription", "models", "nodes", "feeds", "storage"}
    if tab not in valid_tabs:
        tab = "general"
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "settings_tab": tab},
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
    from datetime import datetime

    from cast2md.config.settings import get_settings
    from cast2md.db.models import JobStatus

    stuck_threshold_minutes = get_settings().stuck_threshold_minutes

    with get_db() as conn:
        job_repo = JobRepository(conn)
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)

        # Get job counts
        job_counts = job_repo.count_by_status()
        stuck_count = job_repo.count_stuck_jobs(stuck_threshold_minutes)

        # Get jobs based on filter
        if status == "stuck":
            jobs = job_repo.get_stuck_jobs(stuck_threshold_minutes)
        elif status:
            try:
                job_status = JobStatus(status)
                jobs = job_repo.get_all_jobs(status=job_status, limit=100)
            except ValueError:
                jobs = job_repo.get_all_jobs(limit=100)
        else:
            jobs = job_repo.get_all_jobs(limit=100)

        # Build job info with episode and feed details
        job_list = []
        for job in jobs:
            episode = episode_repo.get_by_id(job.episode_id)
            if not episode:
                continue
            feed = feed_repo.get_by_id(episode.feed_id)

            # Calculate runtime
            runtime_seconds = None
            if job.status == JobStatus.RUNNING and job.started_at:
                runtime_seconds = int((datetime.now() - job.started_at).total_seconds())
            is_stuck = job.is_stuck(stuck_threshold_minutes)

            job_list.append(
                {
                    "job": job,
                    "episode": episode,
                    "feed": feed,
                    "is_stuck": is_stuck,
                    "runtime_seconds": runtime_seconds,
                }
            )

    return templates.TemplateResponse(
        "queue.html",
        {
            "request": request,
            "jobs": job_list,
            "job_counts": job_counts,
            "stuck_count": stuck_count,
            "current_filter": status or "all",
            "stuck_threshold_minutes": stuck_threshold_minutes,
        },
    )


@router.get("/admin/runpod", response_class=HTMLResponse)
def admin_runpod_page(request: Request):
    """Admin RunPod management page."""
    from cast2md.config.settings import get_settings, reload_settings
    from cast2md.db.repository import RunPodModelRepository
    from cast2md.services.runpod_service import get_runpod_service

    # Reload settings to pick up any runtime changes
    reload_settings()
    settings = get_settings()
    service = get_runpod_service()

    # Get effective server URL/IP (auto-derived from Tailscale if not configured)
    effective_server_url = service.get_effective_server_url()
    effective_server_ip = service.get_effective_server_ip()

    # Get RunPod status
    runpod_status = {
        "available": service.is_available(),
        "enabled": service.is_enabled(),
        "can_create": False,
        "can_create_reason": "",
        "max_pods": settings.runpod_max_pods,
        "active_pods": [],
        "setup_states": [],
        "server_url": effective_server_url,
        "server_ip": effective_server_ip,
    }

    if service.is_available():
        can_create, reason = service.can_create_pod()
        runpod_status["can_create"] = can_create
        runpod_status["can_create_reason"] = reason
        active_pods = service.list_pods()
        runpod_status["active_pods"] = [
            {
                "id": p.id,
                "name": p.name,
                "status": p.status,
                "gpu_type": p.gpu_type,
                "created_at": p.created_at,
            }
            for p in active_pods
        ]
        # Include all setup_states - template will filter out duplicates from active_pods
        runpod_status["setup_states"] = [
            {
                "instance_id": s.instance_id,
                "pod_id": s.pod_id,
                "pod_name": s.pod_name,
                "ts_hostname": s.ts_hostname,
                "node_name": s.node_name,
                "gpu_type": s.gpu_type,
                "phase": s.phase.value,
                "message": s.message,
                "error": s.error,
                "host_ip": s.host_ip,
            }
            for s in service.get_setup_states()
        ]

    # Get transcribe queue count (only transcription jobs - what GPU workers handle)
    with get_db() as conn:
        job_repo = JobRepository(conn)
        transcribe_queued = job_repo.count_by_status(JobType.TRANSCRIBE).get("queued", 0)

    # Get GPU types for settings dropdown (with pricing)
    gpu_types = []
    if service.is_available():
        raw_gpus = service.get_available_gpus()
        if raw_gpus:
            gpu_types = [
                {
                    "id": g["id"],
                    "display_name": g["display_name"],
                    "memory_gb": g.get("memory_gb"),
                    "price_hr": g.get("price_hr"),
                }
                for g in raw_gpus
            ]

    # Fallback GPU types if API unavailable (no pricing)
    if not gpu_types:
        gpu_types = [
            {
                "id": "NVIDIA GeForce RTX 4090",
                "display_name": "RTX 4090",
                "memory_gb": 24,
                "price_hr": None,
            },
            {
                "id": "NVIDIA GeForce RTX 3090",
                "display_name": "RTX 3090",
                "memory_gb": 24,
                "price_hr": None,
            },
            {
                "id": "NVIDIA RTX A4000",
                "display_name": "RTX A4000",
                "memory_gb": 16,
                "price_hr": None,
            },
            {
                "id": "NVIDIA GeForce RTX 4080",
                "display_name": "RTX 4080",
                "memory_gb": 16,
                "price_hr": None,
            },
            {"id": "NVIDIA L4", "display_name": "L4", "memory_gb": 24, "price_hr": None},
        ]

    # Get pod run history and stats
    pod_runs = service.get_pod_runs(limit=10) if service.is_available() else []
    pod_run_stats = service.get_pod_run_stats(days=30) if service.is_available() else {}

    # Get transcription models from database
    model_repo = RunPodModelRepository(conn)
    model_repo.seed_defaults()
    transcription_models = [
        {"id": m.id, "display_name": m.display_name, "backend": m.backend}
        for m in model_repo.get_all(enabled_only=True)
    ]

    return templates.TemplateResponse(
        "runpod.html",
        {
            "request": request,
            "runpod_status": runpod_status,
            "settings": settings,
            "transcribe_queued": transcribe_queued,
            "gpu_types": gpu_types,
            "transcription_models": transcription_models,
            "pod_runs": pod_runs,
            "pod_run_stats": pod_run_stats,
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
    index_stats = {
        "total_segments": 0,
        "indexed_episodes": 0,
        "embedded_episodes": 0,
        "total_embeddings": 0,
    }
    feeds = []
    actual_mode = mode
    recent_transcripts = []
    search_items = []

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

            episodes_by_id = episode_repo.get_by_ids([result.episode_id for result in results])
            feed_images = {feed.id: feed.image_url for feed in feeds}
            grouped: dict[int, dict] = {}
            for result in results:
                episode = episodes_by_id.get(result.episode_id)
                if not episode:
                    continue
                timestamp = None if result.segment_start < 0 else int(result.segment_start)
                excerpt = result.text or episode.description or ""
                if result.episode_id not in grouped:
                    grouped[result.episode_id] = {
                        "episode": episode,
                        "feed_title": result.feed_title,
                        "feed_image_url": feed_images.get(result.feed_id),
                        "summary": highlight_query(excerpt, q),
                        "summary_safe": True,
                        "source_label": (
                            "title" if result.result_type == "episode" else result.match_type
                        ),
                        "first_timestamp": timestamp,
                        "more_matches": [],
                    }
                elif len(grouped[result.episode_id]["more_matches"]) < 2:
                    minutes, seconds = divmod(timestamp or 0, 60)
                    grouped[result.episode_id]["more_matches"].append(
                        {
                            "timestamp": f"{minutes:02d}:{seconds:02d}",
                            "text": strip_html(excerpt)[:180],
                        }
                    )
            search_items = list(grouped.values())
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
            "search_items": search_items,
        },
    )
