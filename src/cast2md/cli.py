"""Command-line interface for cast2md."""

from pathlib import Path

import click

from cast2md.config.settings import get_settings
from cast2md.db.connection import get_db, init_db
from cast2md.db.models import EpisodeStatus
from cast2md.db.repository import EpisodeRepository, FeedRepository
from cast2md.download.downloader import download_episode
from cast2md.feed.discovery import discover_new_episodes, validate_feed_url
from cast2md.feed.parser import parse_feed
from cast2md.transcription.service import transcribe_episode


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """cast2md - Podcast transcription service.

    Download podcast episodes via RSS and transcribe them using Whisper.
    """
    pass


@cli.command("init-db")
def cmd_init_db():
    """Initialize the database."""
    settings = get_settings()
    settings.ensure_directories()

    init_db()
    click.echo(f"Database initialized at {settings.database_path}")


@cli.command("add-feed")
@click.argument("url")
def cmd_add_feed(url: str):
    """Add a new podcast feed.

    URL should be an RSS feed URL for a podcast.
    """
    click.echo(f"Validating feed: {url}")

    is_valid, message, parsed = validate_feed_url(url)
    if not is_valid:
        click.echo(f"Error: {message}", err=True)
        raise SystemExit(1)

    click.echo(f"Found podcast: {parsed.title}")
    click.echo(f"Episodes: {len(parsed.episodes)}")

    with get_db() as conn:
        repo = FeedRepository(conn)

        # Check if feed already exists
        existing = repo.get_by_url(url)
        if existing:
            click.echo(f"Feed already exists with ID {existing.id}")
            return

        feed = repo.create(
            url=url,
            title=parsed.title,
            description=parsed.description,
            image_url=parsed.image_url,
        )

        click.echo(f"Added feed with ID {feed.id}")
        click.echo(f"Run 'cast2md poll {feed.id}' to discover episodes")


@cli.command("list-feeds")
def cmd_list_feeds():
    """List all podcast feeds."""
    with get_db() as conn:
        repo = FeedRepository(conn)
        feeds = repo.get_all()

    if not feeds:
        click.echo("No feeds found. Add one with 'cast2md add-feed <url>'")
        return

    click.echo(f"{'ID':<5} {'Title':<50} {'Last Polled':<20}")
    click.echo("-" * 75)

    for feed in feeds:
        last_polled = feed.last_polled.strftime("%Y-%m-%d %H:%M") if feed.last_polled else "Never"
        title = feed.title[:47] + "..." if len(feed.title) > 50 else feed.title
        click.echo(f"{feed.id:<5} {title:<50} {last_polled:<20}")


@cli.command("poll")
@click.argument("feed_id", type=int)
def cmd_poll(feed_id: int):
    """Poll a feed for new episodes.

    FEED_ID is the numeric ID of the feed to poll.
    """
    with get_db() as conn:
        repo = FeedRepository(conn)
        feed = repo.get_by_id(feed_id)

    if not feed:
        click.echo(f"Error: Feed {feed_id} not found", err=True)
        raise SystemExit(1)

    click.echo(f"Polling feed: {feed.title}")

    try:
        new_count = discover_new_episodes(feed)
        click.echo(f"Discovered {new_count} new episodes")
    except Exception as e:
        click.echo(f"Error polling feed: {e}", err=True)
        raise SystemExit(1)


@cli.command("list-episodes")
@click.argument("feed_id", type=int)
@click.option("--limit", "-n", default=20, help="Maximum episodes to show")
def cmd_list_episodes(feed_id: int, limit: int):
    """List episodes for a feed.

    FEED_ID is the numeric ID of the feed.
    """
    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        feed = feed_repo.get_by_id(feed_id)

        if not feed:
            click.echo(f"Error: Feed {feed_id} not found", err=True)
            raise SystemExit(1)

        episode_repo = EpisodeRepository(conn)
        episodes = episode_repo.get_by_feed(feed_id, limit=limit)

    if not episodes:
        click.echo(f"No episodes found for '{feed.title}'")
        click.echo(f"Run 'cast2md poll {feed_id}' to discover episodes")
        return

    click.echo(f"Episodes for: {feed.title}")
    click.echo(f"{'ID':<5} {'Status':<12} {'Published':<12} {'Title':<45}")
    click.echo("-" * 75)

    for ep in episodes:
        pub_date = ep.published_at.strftime("%Y-%m-%d") if ep.published_at else "Unknown"
        title = ep.title[:42] + "..." if len(ep.title) > 45 else ep.title
        click.echo(f"{ep.id:<5} {ep.status.value:<12} {pub_date:<12} {title:<45}")


@cli.command("download")
@click.argument("episode_id", type=int)
def cmd_download(episode_id: int):
    """Download an episode's audio file.

    EPISODE_ID is the numeric ID of the episode.
    """
    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        episode = episode_repo.get_by_id(episode_id)

        if not episode:
            click.echo(f"Error: Episode {episode_id} not found", err=True)
            raise SystemExit(1)

        feed_repo = FeedRepository(conn)
        feed = feed_repo.get_by_id(episode.feed_id)

    click.echo(f"Downloading: {episode.title}")
    click.echo(f"From: {episode.audio_url}")

    try:
        audio_path = download_episode(episode, feed)
        click.echo(f"Downloaded to: {audio_path}")
    except Exception as e:
        click.echo(f"Error downloading: {e}", err=True)
        raise SystemExit(1)


@cli.command("transcribe")
@click.argument("episode_id", type=int)
@click.option("--timestamps", "-t", is_flag=True, help="Include timestamps in output")
def cmd_transcribe(episode_id: int, timestamps: bool):
    """Transcribe an episode's audio.

    EPISODE_ID is the numeric ID of the episode.
    The episode must be downloaded first.
    """
    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        episode = episode_repo.get_by_id(episode_id)

        if not episode:
            click.echo(f"Error: Episode {episode_id} not found", err=True)
            raise SystemExit(1)

        if not episode.audio_path:
            click.echo(f"Error: Episode not downloaded. Run 'cast2md download {episode_id}' first", err=True)
            raise SystemExit(1)

        feed_repo = FeedRepository(conn)
        feed = feed_repo.get_by_id(episode.feed_id)

    click.echo(f"Transcribing: {episode.title}")
    click.echo("Loading Whisper model (this may take a moment)...")

    try:
        transcript_path = transcribe_episode(episode, feed, include_timestamps=timestamps)
        click.echo(f"Transcript saved to: {transcript_path}")
    except Exception as e:
        click.echo(f"Error transcribing: {e}", err=True)
        raise SystemExit(1)


@cli.command("process")
@click.argument("episode_id", type=int)
@click.option("--timestamps", "-t", is_flag=True, help="Include timestamps in output")
def cmd_process(episode_id: int, timestamps: bool):
    """Download and transcribe an episode.

    EPISODE_ID is the numeric ID of the episode.
    This combines the download and transcribe commands.
    """
    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        episode = episode_repo.get_by_id(episode_id)

        if not episode:
            click.echo(f"Error: Episode {episode_id} not found", err=True)
            raise SystemExit(1)

        feed_repo = FeedRepository(conn)
        feed = feed_repo.get_by_id(episode.feed_id)

    click.echo(f"Processing: {episode.title}")

    # Download if needed
    if not episode.audio_path or not Path(episode.audio_path).exists():
        click.echo("Downloading audio...")
        try:
            audio_path = download_episode(episode, feed)
            click.echo(f"Downloaded to: {audio_path}")
            # Refresh episode to get updated audio_path
            with get_db() as conn:
                episode_repo = EpisodeRepository(conn)
                episode = episode_repo.get_by_id(episode_id)
        except Exception as e:
            click.echo(f"Error downloading: {e}", err=True)
            raise SystemExit(1)
    else:
        click.echo(f"Audio already downloaded: {episode.audio_path}")

    # Transcribe
    click.echo("Transcribing (this may take a while)...")
    try:
        transcript_path = transcribe_episode(episode, feed, include_timestamps=timestamps)
        click.echo(f"Transcript saved to: {transcript_path}")
        click.echo("Done!")
    except Exception as e:
        click.echo(f"Error transcribing: {e}", err=True)
        raise SystemExit(1)


@cli.command("status")
def cmd_status():
    """Show system status and statistics."""
    settings = get_settings()

    click.echo("cast2md Status")
    click.echo("=" * 40)

    # Check database
    db_path = settings.database_path
    if db_path.exists():
        click.echo(f"Database: {db_path} (exists)")
    else:
        click.echo(f"Database: {db_path} (not initialized)")
        click.echo("Run 'cast2md init-db' to initialize")
        return

    # Count feeds and episodes
    with get_db() as conn:
        feed_repo = FeedRepository(conn)
        episode_repo = EpisodeRepository(conn)

        feeds = feed_repo.get_all()
        status_counts = episode_repo.count_by_status()

    click.echo(f"Feeds: {len(feeds)}")
    click.echo()
    click.echo("Episodes by status:")

    total = 0
    for status in EpisodeStatus:
        count = status_counts.get(status.value, 0)
        total += count
        click.echo(f"  {status.value:<12}: {count}")

    click.echo(f"  {'total':<12}: {total}")

    click.echo()
    click.echo("Configuration:")
    click.echo(f"  Storage path: {settings.storage_path}")
    click.echo(f"  Whisper model: {settings.whisper_model}")
    click.echo(f"  Whisper device: {settings.whisper_device}")


@cli.command("serve")
@click.option("--host", "-h", default="0.0.0.0", help="Host to bind to")
@click.option("--port", "-p", default=8000, help="Port to bind to")
@click.option("--reload", "-r", is_flag=True, help="Enable auto-reload for development")
def cmd_serve(host: str, port: int, reload: bool):
    """Start the web server."""
    click.echo(f"Starting cast2md web server on http://{host}:{port}")
    click.echo("Press Ctrl+C to stop")

    from cast2md.main import run_server
    run_server(host=host, port=port, reload=reload)


if __name__ == "__main__":
    cli()
