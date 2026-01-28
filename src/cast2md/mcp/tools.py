"""MCP tools (actions) for cast2md."""

import re

from cast2md.mcp import client as remote
from cast2md.mcp.server import mcp


# Patterns for detecting "latest episode" type queries (German + English)
LATEST_PATTERNS = [
    r"\b(letzte|neueste|aktuelle|neue)\s*(folge|episode|ausgabe)n?\b",
    r"\b(latest|newest|recent|last)\s*(episode|folge)s?\b",
    r"\binhalt\s+(der\s+)?(letzten|neuesten)\b",
]
LATEST_RE = re.compile("|".join(LATEST_PATTERNS), re.IGNORECASE)


def _normalize(text: str) -> str:
    """Normalize text for matching: lowercase, hyphens to spaces, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"[-–—]", " ", text)  # Hyphens to spaces
    text = re.sub(r"[''`]", "", text)  # Remove apostrophes
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _find_matching_feed(query: str, feeds: list) -> tuple[dict | None, str]:
    """Try to find a feed mentioned in the query.

    Returns (matched_feed, remaining_query) or (None, original_query).
    Matches against feed titles and authors.
    """
    query_norm = _normalize(query)
    best_match = None
    best_match_len = 0
    match_pattern = ""

    for feed in feeds:
        title = feed.get("title", "") or ""
        author = feed.get("author", "") or ""

        # Normalize title for matching
        title_norm = _normalize(title)

        # Try matching significant parts of the title
        # Generate all contiguous word sequences (n-grams)
        words = title_norm.split()
        for length in range(len(words), 0, -1):
            for start in range(len(words) - length + 1):
                partial = " ".join(words[start:start + length])
                if len(partial) >= 4 and partial in query_norm:
                    if len(partial) > best_match_len:
                        best_match = feed
                        best_match_len = len(partial)
                        match_pattern = partial
                    break
            if best_match_len > 0 and length < best_match_len // 2:
                # Already found a good match, don't look for shorter ones
                break

        # Also try matching author name (for "Peter Attia" -> The Drive, etc.)
        if author:
            author_norm = _normalize(author)
            author_words = author_norm.split()
            for length in range(len(author_words), 0, -1):
                for start in range(len(author_words) - length + 1):
                    partial = " ".join(author_words[start:start + length])
                    if len(partial) >= 4 and partial in query_norm:
                        if len(partial) > best_match_len:
                            best_match = feed
                            best_match_len = len(partial)
                            match_pattern = partial
                        break

    if best_match and match_pattern:
        # Remove the matched text from query (handle both normalized and original forms)
        remaining = query
        # Try to remove the pattern with flexible whitespace/hyphen matching
        pattern = re.escape(match_pattern).replace(r"\ ", r"[\s\-]+")
        remaining = re.sub(pattern, " ", remaining, flags=re.IGNORECASE)
        # Clean up common connecting words
        remaining = re.sub(r"\b(im|in|vom|von|des|der|the|from|about|über)\b", " ", remaining, flags=re.IGNORECASE)
        remaining = re.sub(r"\s+", " ", remaining).strip()
        return best_match, remaining

    return None, query


def _get_feeds_with_authors():
    """Get all feeds with author info for matching."""
    if remote.is_remote_mode():
        return remote.get_feeds()

    from cast2md.db.connection import get_db
    from cast2md.db.repository import EpisodeRepository, FeedRepository

    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)
        feeds = feed_repo.get_all()

        return [
            {
                "id": feed.id,
                "title": feed.display_title,
                "author": feed.author,
                "episode_count": episode_repo.count_by_feed(feed.id),
            }
            for feed in feeds
        ]


@mcp.tool()
def search(query: str) -> dict:
    """Universal search for podcast content.

    This is the main entry point for searching. It automatically:
    - Detects podcast/feed mentions and filters to that feed
    - Recognizes "latest episode" queries and returns transcript
    - Searches episode titles AND transcript content

    Args:
        query: Natural language query. Examples:
            - "Was ist der Inhalt der letzten Folge des KI Podcasts?"
            - "Was wurde im KI Podcast über MCP gesagt?"
            - "Was wird über Protein gesagt?"
            - "Peter Attia über Krafttraining"

    Returns:
        Search results with transcript excerpts, or latest episode content.
    """
    from cast2md.db.connection import get_db
    from cast2md.db.repository import EpisodeRepository, FeedRepository
    from cast2md.search.repository import TranscriptSearchRepository

    # Get all feeds for matching
    feeds = _get_feeds_with_authors()

    # Try to find a feed mentioned in the query
    matched_feed, remaining_query = _find_matching_feed(query, feeds)

    # Check if this is a "latest episode" query
    is_latest_query = bool(LATEST_RE.search(query))

    if is_latest_query:
        # Remove the "latest episode" pattern from remaining query
        remaining_query = LATEST_RE.sub("", remaining_query)
        remaining_query = re.sub(r"\s+", " ", remaining_query).strip()

    # If asking for latest episode of a specific podcast
    if is_latest_query and matched_feed:
        feed_id = matched_feed["id"]

        if remote.is_remote_mode():
            feed_data = remote.get_feed(feed_id)
            if not feed_data or "error" in feed_data:
                return {"error": f"Feed not found: {matched_feed['title']}"}
            episodes = feed_data.get("episodes", [])
            if not episodes:
                return {"error": f"No episodes found for {matched_feed['title']}"}
            latest = episodes[0]  # Already sorted by date
            episode_id = latest["id"]

            # Get transcript
            transcript_data = remote.get_transcript_section(episode_id, None, 600)
            return {
                "query": query,
                "type": "latest_episode",
                "feed": matched_feed["title"],
                "episode_id": episode_id,
                "episode_title": latest.get("title"),
                "published_at": latest.get("published_at"),
                "transcript": transcript_data.get("transcript", "No transcript available"),
            }

        with get_db() as conn:
            episode_repo = EpisodeRepository(conn)
            feed_repo = FeedRepository(conn)

            episodes = episode_repo.get_by_feed(feed_id, limit=1)
            if not episodes:
                return {"error": f"No episodes found for {matched_feed['title']}"}

            latest = episodes[0]
            feed = feed_repo.get_by_id(feed_id)

            # Get transcript content
            if latest.transcript_path:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT segment_start, segment_end, text
                    FROM transcript_segments
                    WHERE episode_id = %s AND segment_start <= 600
                    ORDER BY segment_start
                    """,
                    (latest.id,),
                )
                segments = cursor.fetchall()

                if segments:
                    lines = []
                    for seg_start, seg_end, text in segments:
                        minutes = int(seg_start) // 60
                        seconds = int(seg_start) % 60
                        lines.append(f"[{minutes:02d}:{seconds:02d}] {text}")
                    transcript = "\n".join(lines)
                else:
                    transcript = "Transcript segments not indexed yet"
            else:
                transcript = f"No transcript available. Use queue_episode({latest.id}) to transcribe."

            return {
                "query": query,
                "type": "latest_episode",
                "feed": feed.display_title if feed else matched_feed["title"],
                "episode_id": latest.id,
                "episode_title": latest.title,
                "published_at": latest.published_at.isoformat() if latest.published_at else None,
                "transcript": transcript,
                "hint": f"Use get_transcript({latest.id}, start_time=X) to read other sections",
            }

    # Regular search - use semantic/hybrid search
    search_query = remaining_query if remaining_query else query
    feed_id = matched_feed["id"] if matched_feed else None

    if remote.is_remote_mode():
        results = remote.semantic_search(search_query, feed_id, limit=10, mode="hybrid")
        # Add context about what we searched
        results["parsed"] = {
            "original_query": query,
            "search_terms": search_query,
            "feed_filter": matched_feed["title"] if matched_feed else None,
        }
        return results

    with get_db() as conn:
        search_repo = TranscriptSearchRepository(conn)
        response = search_repo.hybrid_search(
            query=search_query,
            feed_id=feed_id,
            limit=10,
            mode="hybrid",
        )

    return {
        "query": query,
        "parsed": {
            "search_terms": search_query,
            "feed_filter": matched_feed["title"] if matched_feed else None,
        },
        "total": response.total,
        "hint": "Use get_transcript(episode_id, start_time=segment_start) to read more context",
        "results": [
            {
                "episode_id": r.episode_id,
                "episode_title": r.episode_title,
                "feed_title": r.feed_title,
                "published_at": r.published_at,
                "segment_start": r.segment_start,
                "text": r.text[:300] if r.text else None,
                "match_type": r.match_type,
                "result_type": r.result_type,
            }
            for r in response.results
        ],
    }


@mcp.tool()
def list_feeds() -> dict:
    """List all podcast feeds in the library.

    Returns:
        All feeds with their IDs, titles, and episode counts.
    """
    if remote.is_remote_mode():
        return remote.get_feeds()

    from cast2md.db.connection import get_db
    from cast2md.db.repository import EpisodeRepository, FeedRepository

    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)
        feeds = feed_repo.get_all()

        return {
            "total": len(feeds),
            "feeds": [
                {
                    "id": feed.id,
                    "title": feed.display_title,
                    "url": feed.url,
                    "episode_count": episode_repo.count_by_feed(feed.id),
                    "description": feed.description[:200] if feed.description else None,
                }
                for feed in feeds
            ],
        }


@mcp.tool()
def find_feed(name: str) -> dict:
    """Find a podcast feed by name (fuzzy search).

    Use this instead of list_feeds when you know the podcast name.

    Args:
        name: Podcast name to search for (case-insensitive, partial match).

    Returns:
        Matching feeds with their IDs and episode counts.

    Example:
        find_feed("KI Podcast") -> finds "Der KI-Podcast"
    """
    if remote.is_remote_mode():
        # Fall back to list_feeds and filter
        feeds = remote.get_feeds()
        name_lower = name.lower()
        matches = [f for f in feeds if name_lower in f.get("title", "").lower()]
        return {"query": name, "total": len(matches), "feeds": matches}

    from cast2md.db.connection import get_db
    from cast2md.db.repository import EpisodeRepository, FeedRepository

    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)
        feeds = feed_repo.get_all()

        name_lower = name.lower()
        matches = [f for f in feeds if name_lower in f.display_title.lower()]

        return {
            "query": name,
            "total": len(matches),
            "feeds": [
                {
                    "id": feed.id,
                    "title": feed.display_title,
                    "episode_count": episode_repo.count_by_feed(feed.id),
                    "description": feed.description[:200] if feed.description else None,
                }
                for feed in matches
            ],
        }


@mcp.tool()
def get_feed(feed_id: int) -> dict:
    """Get details for a specific podcast feed with its episodes.

    Args:
        feed_id: The feed ID to retrieve.

    Returns:
        Feed details and list of episodes.
    """
    if remote.is_remote_mode():
        return remote.get_feed(feed_id)

    from cast2md.db.connection import get_db
    from cast2md.db.repository import EpisodeRepository, FeedRepository

    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)

        feed = feed_repo.get_by_id(feed_id)
        if not feed:
            return {"error": f"Feed {feed_id} not found"}

        episodes = episode_repo.get_by_feed(feed_id, limit=50)

        return {
            "id": feed.id,
            "title": feed.display_title,
            "url": feed.url,
            "author": feed.author,
            "description": feed.description,
            "episode_count": len(episodes),
            "episodes": [
                {
                    "id": ep.id,
                    "title": ep.title,
                    "published_at": ep.published_at.isoformat() if ep.published_at else None,
                    "status": ep.status.value,
                    "has_transcript": ep.transcript_path is not None,
                }
                for ep in episodes
            ],
        }


@mcp.tool()
def get_episode(episode_id: int) -> dict:
    """Get details for a specific episode.

    Args:
        episode_id: The episode ID to retrieve.

    Returns:
        Full episode details including status and transcript availability.
    """
    if remote.is_remote_mode():
        return remote.get_episode(episode_id)

    from cast2md.db.connection import get_db
    from cast2md.db.repository import EpisodeRepository, FeedRepository

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)

        episode = episode_repo.get_by_id(episode_id)
        if not episode:
            return {"error": f"Episode {episode_id} not found"}

        feed = feed_repo.get_by_id(episode.feed_id)

        return {
            "id": episode.id,
            "title": episode.title,
            "feed_id": episode.feed_id,
            "feed_title": feed.display_title if feed else None,
            "published_at": episode.published_at.isoformat() if episode.published_at else None,
            "duration_seconds": episode.duration_seconds,
            "status": episode.status.value,
            "has_transcript": episode.transcript_path is not None,
            "description": episode.description,
            "audio_url": episode.audio_url,
            "hint": "Use cast2md://episodes/{id}/transcript to read the transcript" if episode.transcript_path else "Use queue_episode(id) to transcribe this episode",
        }


@mcp.tool()
def semantic_search(
    query: str,
    feed_id: int | None = None,
    limit: int = 20,
    mode: str = "hybrid",
) -> dict:
    """Search transcripts using natural language understanding.

    Uses hybrid search combining keyword matching with semantic similarity
    to find conceptually related content even without exact keyword matches.

    Args:
        query: Natural language search query (e.g., "protein and strength",
               "discussions about building muscle").
        feed_id: Optional feed ID to limit search to a specific podcast.
        limit: Maximum number of results to return (default: 20).
        mode: Search mode - "hybrid" (recommended), "semantic", or "keyword".

    Returns:
        Search results with matching segments, scores, and match types.

    Example:
        semantic_search("discussions about building muscle")
        # Returns episodes about weightlifting, nutrition, fitness
        # even if they don't explicitly say "muscle"
    """
    if remote.is_remote_mode():
        return remote.semantic_search(query, feed_id, limit, mode)

    from cast2md.db.connection import get_db
    from cast2md.search.repository import TranscriptSearchRepository

    # Validate mode
    valid_modes = ("hybrid", "semantic", "keyword")
    if mode not in valid_modes:
        mode = "hybrid"

    with get_db() as conn:
        search_repo = TranscriptSearchRepository(conn)
        response = search_repo.hybrid_search(
            query=query,
            feed_id=feed_id,
            limit=limit,
            mode=mode,  # type: ignore
        )

    return {
        "query": response.query,
        "total": response.total,
        "mode": response.mode,
        "hint": "Use get_transcript(episode_id, start_time=segment_start) to read more context around a match",
        "results": [
            {
                "episode_id": r.episode_id,
                "episode_title": r.episode_title,
                "feed_id": r.feed_id,
                "feed_title": r.feed_title,
                "published_at": r.published_at,
                "segment_start": r.segment_start,
                "segment_end": r.segment_end,
                "text": r.text,
                "score": r.score,
                "match_type": r.match_type,
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
        "hint": "Use get_transcript(episode_id) to read transcript, or queue_episode(id) to transcribe if not available",
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
def get_recent_episodes(days: int = 7, limit: int = 50) -> dict:
    """Get recently published episodes across all feeds.

    Args:
        days: Number of days to look back (default: 7).
        limit: Maximum episodes to return (default: 50).

    Returns:
        Recent episodes with feed info, sorted by publish date.
    """
    if remote.is_remote_mode():
        return remote.get_recent_episodes(days, limit)

    from cast2md.db.connection import get_db
    from cast2md.db.repository import EpisodeRepository

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        results = episode_repo.get_recent_episodes(days=days, limit=limit)

    return {
        "days": days,
        "total": len(results),
        "hint": "Use get_transcript(episode_id) to read transcript, or queue_episode(id) to transcribe if not available",
        "results": [
            {
                "id": ep.id,
                "feed_id": ep.feed_id,
                "feed_title": feed_title,
                "title": ep.title,
                "description": ep.description[:500] if ep.description else None,
                "published_at": ep.published_at.isoformat() if ep.published_at else None,
                "status": ep.status.value,
                "has_transcript": ep.transcript_path is not None,
            }
            for ep, feed_title in results
        ],
    }


@mcp.tool()
def get_transcript(
    episode_id: int,
    start_time: float | None = None,
    duration: float = 300,
) -> dict:
    """Get transcript text for an episode, optionally around a specific timestamp.

    Use this to read what was said in a podcast. If you found a match via
    semantic_search, use start_time to get context around that segment.

    Args:
        episode_id: The episode ID to get transcript for.
        start_time: Optional start time in seconds. If provided, returns
                    transcript around this timestamp with context.
        duration: Duration in seconds to return (default: 300 = 5 minutes).
                  Centered around start_time if provided.

    Returns:
        Transcript text with timestamps, or error if not available.

    Example:
        # Get transcript around 25:21 (1521 seconds)
        get_transcript(11110, start_time=1521)
    """
    if remote.is_remote_mode():
        return remote.get_transcript_section(episode_id, start_time, duration)

    from pathlib import Path

    from cast2md.db.connection import get_db
    from cast2md.db.repository import EpisodeRepository, FeedRepository

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)
        episode = episode_repo.get_by_id(episode_id)

        if not episode:
            return {"error": f"Episode {episode_id} not found"}

        if not episode.transcript_path:
            return {
                "error": f"No transcript for '{episode.title}'. Use queue_episode({episode_id}) to process it.",
                "episode_id": episode_id,
                "status": episode.status.value,
            }

        transcript_path = Path(episode.transcript_path)
        if not transcript_path.exists():
            return {"error": f"Transcript file not found at {episode.transcript_path}"}

        feed = feed_repo.get_by_id(episode.feed_id)

        # Read transcript segments from database for timestamp filtering
        cursor = conn.cursor()
        if start_time is not None:
            # Get segments around the specified time
            half_duration = duration / 2
            time_start = max(0, start_time - half_duration)
            time_end = start_time + half_duration

            cursor.execute(
                """
                SELECT segment_start, segment_end, text
                FROM transcript_segments
                WHERE episode_id = %s
                  AND segment_start >= %s
                  AND segment_start <= %s
                ORDER BY segment_start
                """,
                (episode_id, time_start, time_end),
            )
        else:
            # Get first N minutes
            cursor.execute(
                """
                SELECT segment_start, segment_end, text
                FROM transcript_segments
                WHERE episode_id = %s
                  AND segment_start <= %s
                ORDER BY segment_start
                """,
                (episode_id, duration),
            )

        segments = cursor.fetchall()

        if not segments:
            # Fall back to reading file directly
            content = transcript_path.read_text(encoding="utf-8")
            # Truncate if too long
            if len(content) > 10000:
                content = content[:10000] + "\n\n[... truncated, use start_time to read specific sections ...]"
            return {
                "episode_id": episode_id,
                "episode_title": episode.title,
                "feed_title": feed.display_title if feed else None,
                "transcript": content,
            }

        # Format segments with timestamps
        lines = []
        for seg_start, seg_end, text in segments:
            minutes = int(seg_start) // 60
            seconds = int(seg_start) % 60
            lines.append(f"[{minutes:02d}:{seconds:02d}] {text}")

        return {
            "episode_id": episode_id,
            "episode_title": episode.title,
            "feed_title": feed.display_title if feed else None,
            "published_at": episode.published_at.isoformat() if episode.published_at else None,
            "time_range": f"{int(segments[0][0])}s - {int(segments[-1][1])}s" if segments else None,
            "transcript": "\n".join(lines),
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
