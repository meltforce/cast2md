"""MCP resources (read-only data) for cast2md."""

from pathlib import Path

from cast2md.mcp import client as remote
from cast2md.mcp.server import mcp


@mcp.resource("cast2md://feeds")
def list_feeds() -> str:
    """List all podcast feeds.

    Returns a list of all configured podcast feeds with their IDs,
    titles, and episode counts.
    """
    if remote.is_remote_mode():
        feeds = remote.get_feeds()
        lines = ["# Podcast Feeds\n"]
        if not feeds:
            lines.append("No feeds configured. Use the add_feed tool to add a podcast.")
        else:
            for feed in feeds:
                lines.append(f"## {feed.get('display_title') or feed.get('title', 'Unknown')}")
                lines.append(f"- **ID:** {feed['id']}")
                lines.append(f"- **URL:** {feed.get('url', 'N/A')}")
                lines.append(f"- **Episodes:** {feed.get('episode_count', 0)}")
                if feed.get("description"):
                    lines.append(f"- **Description:** {feed['description'][:200]}...")
                lines.append("")
        return "\n".join(lines)

    from cast2md.db.connection import get_db
    from cast2md.db.repository import EpisodeRepository, FeedRepository

    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)
        feeds = feed_repo.get_all()

        lines = ["# Podcast Feeds\n"]

        if not feeds:
            lines.append("No feeds configured. Use the add_feed tool to add a podcast.")
        else:
            for feed in feeds:
                episode_count = episode_repo.count_by_feed(feed.id)
                last_polled = (
                    feed.last_polled.strftime("%Y-%m-%d %H:%M")
                    if feed.last_polled
                    else "Never"
                )
                lines.append(f"## {feed.display_title}")
                lines.append(f"- **ID:** {feed.id}")
                lines.append(f"- **URL:** {feed.url}")
                lines.append(f"- **Episodes:** {episode_count}")
                lines.append(f"- **Last Polled:** {last_polled}")
                if feed.description:
                    lines.append(f"- **Description:** {feed.description[:200]}...")
                lines.append("")

    return "\n".join(lines)


@mcp.resource("cast2md://feeds/{feed_id}")
def get_feed(feed_id: int) -> str:
    """Get details for a specific feed with recent episodes.

    Args:
        feed_id: The feed ID to retrieve.

    Returns:
        Feed details and a list of recent episodes.
    """
    if remote.is_remote_mode():
        feed = remote.get_feed(feed_id)
        if not feed:
            return f"Feed {feed_id} not found."
        lines = [f"# {feed.get('display_title') or feed.get('title', 'Unknown')}\n"]
        lines.append(f"- **ID:** {feed['id']}")
        lines.append(f"- **URL:** {feed.get('url', 'N/A')}")
        if feed.get("author"):
            lines.append(f"- **Author:** {feed['author']}")
        if feed.get("link"):
            lines.append(f"- **Website:** {feed['link']}")
        if feed.get("description"):
            lines.append(f"- **Description:** {feed['description']}")
        lines.append("")
        if feed.get("episodes"):
            lines.append("## Recent Episodes\n")
            for ep in feed["episodes"][:25]:
                status = ep.get("status", "new")
                status_icon = {"new": "[ ]", "awaiting_transcript": "[A]", "needs_audio": "[N]",
                              "downloading": "[D]", "audio_ready": "[d]",
                              "transcribing": "[T]", "completed": "[x]", "failed": "[!]"}.get(status, "[ ]")
                pub_date = ep.get("published_at", "Unknown")[:10] if ep.get("published_at") else "Unknown"
                lines.append(f"- {status_icon} **{ep['title']}** (ID: {ep['id']})")
                lines.append(f"  - Published: {pub_date} | Status: {status}")
        return "\n".join(lines)

    from cast2md.db.connection import get_db
    from cast2md.db.models import EpisodeStatus
    from cast2md.db.repository import EpisodeRepository, FeedRepository

    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)

        feed = feed_repo.get_by_id(feed_id)
        if not feed:
            return f"Feed {feed_id} not found."

        episodes = episode_repo.get_by_feed(feed_id, limit=25)

        lines = [f"# {feed.display_title}\n"]
        lines.append(f"- **ID:** {feed.id}")
        lines.append(f"- **URL:** {feed.url}")
        if feed.author:
            lines.append(f"- **Author:** {feed.author}")
        if feed.link:
            lines.append(f"- **Website:** {feed.link}")
        if feed.description:
            lines.append(f"- **Description:** {feed.description}")
        if feed.category_list:
            lines.append(f"- **Categories:** {', '.join(feed.category_list)}")
        lines.append(
            f"- **Last Polled:** {feed.last_polled.strftime('%Y-%m-%d %H:%M') if feed.last_polled else 'Never'}"
        )
        lines.append("")

        lines.append("## Recent Episodes\n")
        if not episodes:
            lines.append("No episodes found. Use refresh_feed to discover episodes.")
        else:
            for ep in episodes:
                status_icon = {
                    EpisodeStatus.NEW: "[ ]",
                    EpisodeStatus.AWAITING_TRANSCRIPT: "[A]",
                    EpisodeStatus.NEEDS_AUDIO: "[N]",
                    EpisodeStatus.DOWNLOADING: "[D]",
                    EpisodeStatus.AUDIO_READY: "[d]",
                    EpisodeStatus.TRANSCRIBING: "[T]",
                    EpisodeStatus.COMPLETED: "[x]",
                    EpisodeStatus.FAILED: "[!]",
                }.get(ep.status, "[ ]")

                pub_date = (
                    ep.published_at.strftime("%Y-%m-%d") if ep.published_at else "Unknown"
                )
                lines.append(f"- {status_icon} **{ep.title}** (ID: {ep.id})")
                lines.append(f"  - Published: {pub_date} | Status: {ep.status.value}")

    return "\n".join(lines)


@mcp.resource("cast2md://episodes/{episode_id}")
def get_episode(episode_id: int) -> str:
    """Get details for a specific episode.

    Args:
        episode_id: The episode ID to retrieve.

    Returns:
        Full episode details including processing status.
    """
    if remote.is_remote_mode():
        episode = remote.get_episode(episode_id)
        if not episode:
            return f"Episode {episode_id} not found."
        lines = [f"# {episode['title']}\n"]
        lines.append(f"- **ID:** {episode['id']}")
        lines.append(f"- **Feed ID:** {episode.get('feed_id', 'Unknown')}")
        lines.append(f"- **Status:** {episode.get('status', 'unknown')}")
        if episode.get("published_at"):
            lines.append(f"- **Published:** {episode['published_at'][:16]}")
        if episode.get("duration_seconds"):
            minutes = episode["duration_seconds"] // 60
            seconds = episode["duration_seconds"] % 60
            lines.append(f"- **Duration:** {minutes}m {seconds}s")
        if episode.get("description"):
            lines.append("")
            lines.append("## Description")
            lines.append(episode["description"])
        return "\n".join(lines)

    from cast2md.db.connection import get_db
    from cast2md.db.repository import EpisodeRepository, FeedRepository

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)

        episode = episode_repo.get_by_id(episode_id)
        if not episode:
            return f"Episode {episode_id} not found."

        feed = feed_repo.get_by_id(episode.feed_id)
        feed_title = feed.display_title if feed else "Unknown"

        lines = [f"# {episode.title}\n"]
        lines.append(f"- **ID:** {episode.id}")
        lines.append(f"- **Feed:** {feed_title} (ID: {episode.feed_id})")
        lines.append(f"- **Status:** {episode.status.value}")
        if episode.published_at:
            lines.append(f"- **Published:** {episode.published_at.strftime('%Y-%m-%d %H:%M')}")
        if episode.duration_seconds:
            minutes = episode.duration_seconds // 60
            seconds = episode.duration_seconds % 60
            lines.append(f"- **Duration:** {minutes}m {seconds}s")
        if episode.author:
            lines.append(f"- **Author:** {episode.author}")
        if episode.link:
            lines.append(f"- **Episode Link:** {episode.link}")
        lines.append(f"- **Audio URL:** {episode.audio_url}")

        lines.append("")
        lines.append("## Processing Status")
        lines.append(f"- **Audio Downloaded:** {'Yes' if episode.audio_path else 'No'}")
        lines.append(f"- **Transcript Available:** {'Yes' if episode.transcript_path else 'No'}")
        if episode.error_message:
            lines.append(f"- **Error:** {episode.error_message}")

        if episode.description:
            lines.append("")
            lines.append("## Description")
            lines.append(episode.description)

    return "\n".join(lines)


@mcp.resource("cast2md://episodes/{episode_id}/transcript")
def get_transcript(episode_id: int) -> str:
    """Get the full transcript for an episode.

    Args:
        episode_id: The episode ID to get the transcript for.

    Returns:
        The full transcript text or an error message if not available.
    """
    if remote.is_remote_mode():
        transcript = remote.get_transcript(episode_id)
        if transcript is None:
            return f"No transcript available for episode {episode_id}. Use queue_episode to process it."
        return transcript

    from cast2md.db.connection import get_db
    from cast2md.db.repository import EpisodeRepository

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        episode = episode_repo.get_by_id(episode_id)

    if not episode:
        return f"Episode {episode_id} not found."

    if not episode.transcript_path:
        return f"No transcript available for episode '{episode.title}'. Use queue_episode to process it."

    transcript_path = Path(episode.transcript_path)
    if not transcript_path.exists():
        return f"Transcript file not found at {episode.transcript_path}"

    # Read and return the transcript
    content = transcript_path.read_text(encoding="utf-8")

    # Add header with episode info
    header = f"# Transcript: {episode.title}\n\n"
    if episode.published_at:
        header += f"*Published: {episode.published_at.strftime('%Y-%m-%d')}*\n\n"
    header += "---\n\n"

    return header + content


@mcp.resource("cast2md://status")
def get_status() -> str:
    """Get system status overview.

    Returns an overview of the cast2md system including database
    statistics, queue status, and configuration.
    """
    if remote.is_remote_mode():
        status = remote.get_status()
        lines = ["# cast2md Status (Remote)\n"]
        lines.append(f"- **API URL:** {remote.API_URL}")
        lines.append(f"- **Status:** {'OK' if status.get('status') == 'healthy' else 'Unknown'}")
        return "\n".join(lines)

    from cast2md.config.settings import get_settings
    from cast2md.db.connection import get_db
    from cast2md.db.models import EpisodeStatus
    from cast2md.db.repository import EpisodeRepository, FeedRepository, JobRepository
    from cast2md.search.repository import TranscriptSearchRepository

    settings = get_settings()

    lines = ["# cast2md Status\n"]

    # Database status
    db_path = settings.database_path
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        lines.append("## Database")
        lines.append(f"- **Path:** {db_path}")
        lines.append(f"- **Size:** {size_mb:.2f} MB")
        lines.append("")
    else:
        lines.append("## Database")
        lines.append("- **Status:** Not initialized")
        lines.append("")
        return "\n".join(lines)

    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)
        job_repo = JobRepository(conn)
        search_repo = TranscriptSearchRepository(conn)

        feeds = feed_repo.get_all()
        episode_counts = episode_repo.count_by_status()
        job_counts = job_repo.count_by_status()
        indexed_segments = search_repo.get_indexed_count()

        # Feeds summary
        lines.append("## Feeds")
        lines.append(f"- **Total Feeds:** {len(feeds)}")
        lines.append("")

        # Episodes summary
        lines.append("## Episodes")
        total_episodes = sum(episode_counts.values())
        lines.append(f"- **Total:** {total_episodes}")
        for status in EpisodeStatus:
            count = episode_counts.get(status.value, 0)
            if count > 0:
                lines.append(f"- **{status.value.title()}:** {count}")
        lines.append("")

        # Queue summary
        lines.append("## Processing Queue")
        queued = job_counts.get("queued", 0)
        running = job_counts.get("running", 0)
        completed = job_counts.get("completed", 0)
        failed = job_counts.get("failed", 0)
        lines.append(f"- **Queued:** {queued}")
        lines.append(f"- **Running:** {running}")
        lines.append(f"- **Completed:** {completed}")
        lines.append(f"- **Failed:** {failed}")
        lines.append("")

        # Search index
        lines.append("## Search Index")
        indexed_episodes = len(search_repo.get_indexed_episodes())
        lines.append(f"- **Indexed Episodes:** {indexed_episodes}")
        lines.append(f"- **Indexed Segments:** {indexed_segments}")
        lines.append("")

    # Configuration
    lines.append("## Configuration")
    lines.append(f"- **Storage Path:** {settings.storage_path}")
    lines.append(f"- **Whisper Model:** {settings.whisper_model}")
    lines.append(f"- **Whisper Device:** {settings.whisper_device}")

    return "\n".join(lines)
