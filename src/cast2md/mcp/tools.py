"""MCP tools (actions) for cast2md."""

from cast2md.mcp import client as remote
from cast2md.mcp.server import mcp


@mcp.tool()
def search_transcripts(
    query: str,
    feed_id: int | None = None,
    limit: int = 20,
) -> dict:
    """Search across all podcast transcripts using full-text search.

    Args:
        query: Search query (supports FTS5 syntax: phrases with quotes, AND, OR, NOT).
        feed_id: Optional feed ID to limit search to a specific podcast.
        limit: Maximum number of results to return (default: 20).

    Returns:
        Search results with matching transcript segments and episode info.
    """
    if remote.is_remote_mode():
        return remote.search_transcripts(query, feed_id, limit)

    from cast2md.db.connection import get_db
    from cast2md.search.repository import TranscriptSearchRepository

    with get_db() as conn:
        search_repo = TranscriptSearchRepository(conn)
        response = search_repo.search(query=query, feed_id=feed_id, limit=limit)

    return {
        "query": response.query,
        "total": response.total,
        "hint": "Use cast2md://episodes/{episode_id}/transcript to read full transcript",
        "results": [
            {
                "episode_id": r.episode_id,
                "episode_title": r.episode_title,
                "feed_id": r.feed_id,
                "feed_title": r.feed_title,
                "published_at": r.published_at,
                "segment_start": r.segment_start,
                "segment_end": r.segment_end,
                "snippet": r.snippet,
            }
            for r in response.results
        ],
    }


@mcp.tool()
def search_episodes(
    query: str,
    feed_id: int | None = None,
    limit: int = 25,
) -> dict:
    """Search episodes by title and description using full-text search.

    Args:
        query: Search query for episode titles and descriptions.
        feed_id: Optional feed ID to limit search to a specific podcast.
        limit: Maximum number of results to return (default: 25).

    Returns:
        Matching episodes with their details.
    """
    if remote.is_remote_mode():
        return remote.search_episodes(query, feed_id, limit)

    from cast2md.db.connection import get_db
    from cast2md.db.repository import EpisodeRepository

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        episodes, total = episode_repo.search_episodes_fts_full(
            query=query,
            feed_id=feed_id,
            limit=limit,
        )

    return {
        "query": query,
        "total": total,
        "hint": "Use queue_episode(id) to transcribe, or cast2md://episodes/{id}/transcript to read existing transcript",
        "results": [
            {
                "id": ep.id,
                "feed_id": ep.feed_id,
                "title": ep.title,
                "description": ep.description[:500] if ep.description else None,
                "published_at": ep.published_at.isoformat() if ep.published_at else None,
                "status": ep.status.value,
                "has_transcript": ep.transcript_path is not None,
            }
            for ep in episodes
        ],
    }


@mcp.tool()
def queue_episode(episode_id: int) -> dict:
    """Queue an episode for download and transcription.

    Args:
        episode_id: The ID of the episode to queue.

    Returns:
        Status of the queue operation.
    """
    if remote.is_remote_mode():
        return remote.queue_episode(episode_id)

    from cast2md.db.connection import get_db
    from cast2md.db.models import JobType
    from cast2md.db.repository import EpisodeRepository, JobRepository

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        job_repo = JobRepository(conn)

        episode = episode_repo.get_by_id(episode_id)
        if not episode:
            return {"success": False, "error": f"Episode {episode_id} not found"}

        # Check if already has a pending download job
        if job_repo.has_pending_job(episode_id, JobType.DOWNLOAD):
            return {
                "success": False,
                "error": "Episode already has a pending download job",
            }

        # Check if already downloaded but needs transcription
        if episode.audio_path and not episode.transcript_path:
            if job_repo.has_pending_job(episode_id, JobType.TRANSCRIBE):
                return {
                    "success": False,
                    "error": "Episode already has a pending transcription job",
                }
            job = job_repo.create(
                episode_id=episode_id,
                job_type=JobType.TRANSCRIBE,
                priority=5,
            )
            return {
                "success": True,
                "message": f"Queued transcription job for '{episode.title}'",
                "job_id": job.id,
                "job_type": "transcribe",
            }

        # Check if already completed
        if episode.transcript_path:
            return {
                "success": False,
                "error": "Episode already has a transcript",
            }

        # Queue download job
        job = job_repo.create(
            episode_id=episode_id,
            job_type=JobType.DOWNLOAD,
            priority=5,
        )

    return {
        "success": True,
        "message": f"Queued download job for '{episode.title}'",
        "job_id": job.id,
        "job_type": "download",
    }


@mcp.tool()
def get_queue_status() -> dict:
    """Get the current status of the processing queue.

    Returns:
        Queue statistics and active/pending jobs.
    """
    if remote.is_remote_mode():
        return remote.get_queue_status()

    from cast2md.db.connection import get_db
    from cast2md.db.models import JobStatus, JobType
    from cast2md.db.repository import EpisodeRepository, JobRepository

    with get_db() as conn:
        job_repo = JobRepository(conn)
        episode_repo = EpisodeRepository(conn)

        # Get counts by status
        status_counts = job_repo.count_by_status()

        # Get running jobs
        running_download = job_repo.get_running_jobs(JobType.DOWNLOAD)
        running_transcribe = job_repo.get_running_jobs(JobType.TRANSCRIBE)

        # Get queued jobs
        queued_jobs = job_repo.get_queued_jobs(limit=10)

        # Build running jobs info
        running = []
        for job in running_download + running_transcribe:
            episode = episode_repo.get_by_id(job.episode_id)
            running.append({
                "job_id": job.id,
                "episode_id": job.episode_id,
                "episode_title": episode.title if episode else "Unknown",
                "job_type": job.job_type.value,
                "started_at": job.started_at.isoformat() if job.started_at else None,
            })

        # Build queued jobs info
        queued = []
        for job in queued_jobs:
            episode = episode_repo.get_by_id(job.episode_id)
            queued.append({
                "job_id": job.id,
                "episode_id": job.episode_id,
                "episode_title": episode.title if episode else "Unknown",
                "job_type": job.job_type.value,
                "priority": job.priority,
            })

    return {
        "counts": {
            "queued": status_counts.get(JobStatus.QUEUED.value, 0),
            "running": status_counts.get(JobStatus.RUNNING.value, 0),
            "completed": status_counts.get(JobStatus.COMPLETED.value, 0),
            "failed": status_counts.get(JobStatus.FAILED.value, 0),
        },
        "running_jobs": running,
        "queued_jobs": queued,
    }


@mcp.tool()
def add_feed(url: str) -> dict:
    """Add a new podcast feed by RSS URL.

    Args:
        url: The RSS feed URL of the podcast to add.

    Returns:
        Result of the add operation with feed details.
    """
    if remote.is_remote_mode():
        return remote.add_feed(url)

    from cast2md.db.connection import get_db
    from cast2md.db.repository import FeedRepository
    from cast2md.feed.discovery import validate_feed_url

    # Validate the feed URL
    is_valid, message, parsed = validate_feed_url(url)
    if not is_valid:
        return {"success": False, "error": message}

    with get_db() as conn:
        feed_repo = FeedRepository(conn)

        # Check if feed already exists
        existing = feed_repo.get_by_url(url)
        if existing:
            return {
                "success": False,
                "error": f"Feed already exists with ID {existing.id}",
                "feed_id": existing.id,
            }

        # Create the feed
        feed = feed_repo.create(
            url=url,
            title=parsed.title,
            description=parsed.description,
            image_url=parsed.image_url,
        )

    return {
        "success": True,
        "message": f"Added feed '{parsed.title}' with {len(parsed.episodes)} episodes",
        "feed_id": feed.id,
        "title": parsed.title,
        "episode_count": len(parsed.episodes),
    }


@mcp.tool()
def refresh_feed(feed_id: int, auto_queue: bool = False) -> dict:
    """Refresh a feed to discover new episodes.

    Args:
        feed_id: The ID of the feed to refresh.
        auto_queue: Whether to automatically queue new episodes for processing.

    Returns:
        Result with count of new episodes discovered.
    """
    if remote.is_remote_mode():
        return remote.refresh_feed(feed_id, auto_queue)

    from cast2md.db.connection import get_db
    from cast2md.db.repository import FeedRepository
    from cast2md.feed.discovery import discover_new_episodes

    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        feed = feed_repo.get_by_id(feed_id)

    if not feed:
        return {"success": False, "error": f"Feed {feed_id} not found"}

    try:
        result = discover_new_episodes(feed, auto_queue=auto_queue)
    except Exception as e:
        return {"success": False, "error": f"Failed to refresh feed: {e}"}

    return {
        "success": True,
        "message": f"Discovered {result.total_new} new episodes",
        "new_episode_ids": result.new_episode_ids,
        "new_episode_count": result.total_new,
        "auto_queued": auto_queue,
    }
