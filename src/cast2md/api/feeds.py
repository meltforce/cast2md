"""Feed API endpoints."""

import io
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, HttpUrl

from cast2md.db.connection import get_db
from cast2md.db.models import Feed
from cast2md.db.repository import EpisodeRepository, FeedRepository
from cast2md.export.formats import export_transcript
from cast2md.feed.discovery import discover_new_episodes, validate_feed_url
from cast2md.feed.itunes import resolve_feed_url
from cast2md.worker.manager import WorkerManager

router = APIRouter(prefix="/api/feeds", tags=["feeds"])


class FeedCreate(BaseModel):
    """Request model for creating a feed."""

    url: HttpUrl


class FeedUpdate(BaseModel):
    """Request model for updating a feed."""

    custom_title: str | None = None


class FeedResponse(BaseModel):
    """Response model for a feed."""

    id: int
    url: str
    title: str
    description: str | None
    image_url: str | None
    custom_title: str | None
    display_title: str
    author: str | None
    link: str | None
    categories: list[str]
    last_polled: str | None
    episode_count: int = 0
    created_at: str
    updated_at: str

    @classmethod
    def from_feed(cls, feed: Feed, episode_count: int = 0) -> "FeedResponse":
        return cls(
            id=feed.id,
            url=feed.url,
            title=feed.title,
            description=feed.description,
            image_url=feed.image_url,
            custom_title=feed.custom_title,
            display_title=feed.display_title,
            author=feed.author,
            link=feed.link,
            categories=feed.category_list,
            last_polled=feed.last_polled.isoformat() if feed.last_polled else None,
            episode_count=episode_count,
            created_at=feed.created_at.isoformat(),
            updated_at=feed.updated_at.isoformat(),
        )


class FeedListResponse(BaseModel):
    """Response model for feed list."""

    feeds: list[FeedResponse]


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str


class PollResponse(BaseModel):
    """Response for poll operation."""

    new_episodes: int
    queued_episodes: int
    message: str


@router.get("", response_model=FeedListResponse)
def list_feeds():
    """List all feeds."""
    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)
        feeds = feed_repo.get_all()

        response_feeds = []
        for feed in feeds:
            episode_count = episode_repo.count_by_feed(feed.id)
            response_feeds.append(FeedResponse.from_feed(feed, episode_count))

    return FeedListResponse(feeds=response_feeds)


@router.post("", response_model=FeedResponse, status_code=201)
def create_feed(feed_data: FeedCreate):
    """Add a new feed and auto-queue all episodes for transcript download.

    Accepts either:
    - Direct RSS feed URL (e.g., https://example.com/podcast.xml)
    - Apple Podcasts URL (e.g., https://podcasts.apple.com/us/podcast/name/id1234567890)

    For Apple Podcasts URLs, the RSS feed URL is resolved via iTunes Lookup API.
    All episodes are queued for transcript download (tries external providers first).
    """
    input_url = str(feed_data.url)

    # Resolve iTunes URL to RSS feed URL if needed
    try:
        rss_url, itunes_id = resolve_feed_url(input_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Validate feed
    is_valid, message, parsed = validate_feed_url(rss_url)
    if not is_valid:
        raise HTTPException(status_code=400, detail=message)

    with get_db() as conn:
        repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)

        # Check for duplicates (using resolved RSS URL)
        existing = repo.get_by_url(rss_url)
        if existing:
            raise HTTPException(status_code=409, detail="Feed already exists")

        categories_json = json.dumps(parsed.categories) if parsed.categories else None
        feed = repo.create(
            url=rss_url,
            title=parsed.title,
            description=parsed.description,
            image_url=parsed.image_url,
            author=parsed.author,
            link=parsed.link,
            categories=categories_json,
            itunes_id=itunes_id,
        )

    # Pause transcript download workers to avoid DB contention during discovery
    manager = WorkerManager()
    manager.pause_transcript_downloads()
    try:
        # Discover episodes and auto-queue ALL for transcript download
        # External transcripts are fast to check, so queue everything
        result = discover_new_episodes(feed, auto_queue=True, queue_only_latest=False)
    finally:
        manager.resume_transcript_downloads()

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        episode_count = episode_repo.count_by_feed(feed.id)

    return FeedResponse.from_feed(feed, episode_count)


@router.get("/{feed_id}", response_model=FeedResponse)
def get_feed(feed_id: int):
    """Get a feed by ID."""
    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)

        feed = feed_repo.get_by_id(feed_id)
        if not feed:
            raise HTTPException(status_code=404, detail="Feed not found")

        episode_count = episode_repo.count_by_feed(feed_id)

    return FeedResponse.from_feed(feed, episode_count)


@router.patch("/{feed_id}", response_model=FeedResponse)
def update_feed(feed_id: int, feed_data: FeedUpdate):
    """Update feed custom title and rename storage directories."""
    with get_db() as conn:
        repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)

        feed = repo.get_by_id(feed_id)
        if not feed:
            raise HTTPException(status_code=404, detail="Feed not found")

        # Get old display_title before update
        old_display_title = feed.display_title
        new_custom_title = feed_data.custom_title

        # Determine new display_title
        new_display_title = new_custom_title if new_custom_title else feed.title

        # Rename directories and update episode paths if display_title changed
        if old_display_title != new_display_title:
            from cast2md.storage.filesystem import (
                rename_podcast_directories,
                sanitize_podcast_name,
            )

            old_dir_name = sanitize_podcast_name(old_display_title)
            new_dir_name = sanitize_podcast_name(new_display_title)

            # Only proceed if sanitized names are different
            if old_dir_name != new_dir_name:
                try:
                    rename_podcast_directories(old_display_title, new_display_title)
                except OSError as e:
                    raise HTTPException(status_code=409, detail=str(e))

                # Update episode paths in database to match new directory names
                episode_repo.update_paths_for_feed_rename(
                    feed_id, old_dir_name, new_dir_name
                )

        # Update database
        feed = repo.update(feed_id, custom_title=new_custom_title)

        episode_count = episode_repo.count_by_feed(feed_id)
        return FeedResponse.from_feed(feed, episode_count)


@router.delete("/{feed_id}", response_model=MessageResponse)
def delete_feed(feed_id: int):
    """Delete a feed and its episodes. Files are moved to trash."""
    with get_db() as conn:
        repo = FeedRepository(conn)

        feed = repo.get_by_id(feed_id)
        if not feed:
            raise HTTPException(status_code=404, detail="Feed not found")

        # Move files to trash before deleting from database
        from cast2md.storage.filesystem import move_feed_to_trash

        trash_path = move_feed_to_trash(feed_id, feed.display_title)

        deleted = repo.delete(feed_id)
        if not deleted:
            raise HTTPException(status_code=500, detail="Failed to delete feed")

    if trash_path:
        return MessageResponse(message=f"Feed '{feed.title}' deleted. Files moved to trash.")
    return MessageResponse(message=f"Feed '{feed.title}' deleted")


@router.post("/{feed_id}/refresh", response_model=PollResponse)
def refresh_feed(feed_id: int, auto_queue: bool = True):
    """Poll a feed for new episodes and optionally auto-queue them."""
    with get_db() as conn:
        repo = FeedRepository(conn)
        feed = repo.get_by_id(feed_id)

    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")

    # Pause transcript download workers to avoid DB contention during discovery
    manager = WorkerManager()
    manager.pause_transcript_downloads()
    try:
        result = discover_new_episodes(feed, auto_queue=auto_queue, queue_only_latest=False)
        queued = len(result.new_episode_ids) if auto_queue else 0
        return PollResponse(
            new_episodes=result.total_new,
            queued_episodes=queued,
            message=f"Discovered {result.total_new} new episodes, queued {queued} for processing",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to poll feed: {e}")
    finally:
        manager.resume_transcript_downloads()


@router.get("/{feed_id}/export")
def export_feed_transcripts(feed_id: int, format: str = "md"):
    """Export all transcripts for a feed as a zip file.

    Supported formats: md, txt, srt, vtt, json
    """
    valid_formats = ["md", "txt", "srt", "vtt", "json"]
    if format not in valid_formats:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid format. Valid options: {valid_formats}",
        )

    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)

        feed = feed_repo.get_by_id(feed_id)
        if not feed:
            raise HTTPException(status_code=404, detail="Feed not found")

        # Fetch all episodes to filter for those with transcripts
        # Note: For feeds with many episodes, consider adding a repository method
        # to query only completed episodes directly
        episodes = episode_repo.get_by_feed(feed_id, limit=10000)

    # Filter episodes with transcripts
    episodes_with_transcripts = [
        ep for ep in episodes
        if ep.transcript_path and Path(ep.transcript_path).exists()
    ]

    if not episodes_with_transcripts:
        raise HTTPException(
            status_code=404,
            detail="No transcripts available for this feed",
        )

    # Create zip file in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for episode in episodes_with_transcripts:
            transcript_path = Path(episode.transcript_path)
            try:
                content, filename, _ = export_transcript(transcript_path, format)
                zf.writestr(filename, content)
            except Exception:
                # Skip failed exports
                continue

    zip_buffer.seek(0)

    # Sanitize feed title for filename
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in feed.title)
    safe_title = safe_title[:50].strip()

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_title}_transcripts.zip"'
        },
    )
