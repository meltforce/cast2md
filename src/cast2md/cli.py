"""Command-line interface for cast2md."""

import os
import shutil
import subprocess
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import click


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
    from cast2md.config.settings import get_settings
    from cast2md.db.connection import init_db

    settings = get_settings()
    settings.ensure_directories()

    init_db()
    click.echo("Database initialized")


@cli.command("add-feed")
@click.argument("url")
def cmd_add_feed(url: str):
    """Add a new podcast feed.

    URL should be an RSS feed URL for a podcast.
    """
    from cast2md.db.connection import get_db
    from cast2md.db.repository import FeedRepository
    from cast2md.feed.discovery import validate_feed_url

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
    from cast2md.db.connection import get_db
    from cast2md.db.repository import FeedRepository

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
    from cast2md.db.connection import get_db
    from cast2md.db.repository import FeedRepository
    from cast2md.feed.discovery import discover_new_episodes

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
    from cast2md.db.connection import get_db
    from cast2md.db.repository import EpisodeRepository, FeedRepository

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
    from cast2md.db.connection import get_db
    from cast2md.db.repository import EpisodeRepository, FeedRepository
    from cast2md.download.downloader import download_episode

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
    from cast2md.db.connection import get_db
    from cast2md.db.repository import EpisodeRepository, FeedRepository
    from cast2md.transcription.service import transcribe_episode

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
    from cast2md.db.connection import get_db
    from cast2md.db.repository import EpisodeRepository, FeedRepository
    from cast2md.download.downloader import download_episode
    from cast2md.transcription.service import transcribe_episode

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
    from cast2md.config.settings import get_settings
    from cast2md.db.connection import get_db
    from cast2md.db.models import EpisodeStatus
    from cast2md.db.repository import EpisodeRepository, FeedRepository

    settings = get_settings()

    click.echo("cast2md Status")
    click.echo("=" * 40)

    # Check database connection
    try:
        with get_db() as conn:
            feed_repo = FeedRepository(conn)
            episode_repo = EpisodeRepository(conn)

            feeds = feed_repo.get_all()
            status_counts = episode_repo.count_by_status()

        click.echo("Database: Connected (PostgreSQL)")
    except Exception as e:
        click.echo(f"Database: Connection failed - {e}")
        click.echo("Check DATABASE_URL environment variable")
        return

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


@cli.command("backup")
@click.option("--output", "-o", type=click.Path(), help="Custom output path for backup")
def cmd_backup(output: str | None):
    """Create a database backup using pg_dump.

    Creates a consistent backup of the PostgreSQL database.
    By default, saves to data/backups/ with a timestamp.

    Requires pg_dump to be installed and DATABASE_URL to be set.
    """
    from cast2md.config.settings import get_settings
    from cast2md.db.config import get_database_config

    config = get_database_config()
    settings = get_settings()

    # Determine backup path
    if output:
        backup_path = Path(output)
    else:
        backup_dir = settings.storage_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"cast2md_backup_{timestamp}.sql"

    click.echo(f"Backing up database to: {backup_path}")

    try:
        # Build pg_dump command
        env = os.environ.copy()
        env["PGPASSWORD"] = config.password

        cmd = [
            "pg_dump",
            "-h", config.host,
            "-p", str(config.port),
            "-U", config.user,
            "-d", config.database,
            "-f", str(backup_path),
            "--no-owner",
            "--no-privileges",
        ]

        result = subprocess.run(cmd, env=env, capture_output=True, text=True)

        if result.returncode != 0:
            click.echo(f"Error: pg_dump failed: {result.stderr}", err=True)
            raise SystemExit(1)

        # Get file size for confirmation
        size_mb = backup_path.stat().st_size / (1024 * 1024)
        click.echo(f"Backup complete: {size_mb:.2f} MB")
    except FileNotFoundError:
        click.echo("Error: pg_dump not found. Install PostgreSQL client tools.", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"Error creating backup: {e}", err=True)
        raise SystemExit(1)


@cli.command("restore")
@click.argument("backup_file", type=click.Path(exists=True))
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
def cmd_restore(backup_file: str, force: bool):
    """Restore database from a backup using psql.

    BACKUP_FILE is the path to the SQL backup file to restore from.

    WARNING: This will overwrite the current database!
    Requires psql to be installed and DATABASE_URL to be set.
    """
    from cast2md.config.settings import get_settings
    from cast2md.db.config import get_database_config

    config = get_database_config()
    backup_path = Path(backup_file)

    # Validate backup file is a SQL file
    if not backup_path.suffix == ".sql":
        click.echo("Warning: Backup file does not have .sql extension", err=True)

    click.echo(f"Backup file: {backup_path}")
    click.echo(f"Target database: {config.database}@{config.host}")

    if not force:
        click.confirm("This will overwrite the current database. Continue?", abort=True)

    try:
        env = os.environ.copy()
        env["PGPASSWORD"] = config.password

        # First create a pre-restore backup
        settings = get_settings()
        backup_dir = settings.storage_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pre_restore_path = backup_dir / f"cast2md_pre_restore_{timestamp}.sql"

        click.echo(f"Creating pre-restore backup: {pre_restore_path}")
        dump_cmd = [
            "pg_dump",
            "-h", config.host,
            "-p", str(config.port),
            "-U", config.user,
            "-d", config.database,
            "-f", str(pre_restore_path),
            "--no-owner",
            "--no-privileges",
        ]
        subprocess.run(dump_cmd, env=env, capture_output=True)

        # Restore using psql
        click.echo("Restoring database...")
        restore_cmd = [
            "psql",
            "-h", config.host,
            "-p", str(config.port),
            "-U", config.user,
            "-d", config.database,
            "-f", str(backup_path),
            "-q",  # Quiet mode
        ]

        result = subprocess.run(restore_cmd, env=env, capture_output=True, text=True)

        if result.returncode != 0:
            click.echo(f"Error: psql restore failed: {result.stderr}", err=True)
            raise SystemExit(1)

        click.echo("Database restored successfully")
        click.echo("Note: Restart the server if it's running")
    except FileNotFoundError:
        click.echo("Error: psql not found. Install PostgreSQL client tools.", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"Error restoring backup: {e}", err=True)
        raise SystemExit(1)


@cli.command("list-backups")
def cmd_list_backups():
    """List available database backups."""
    from cast2md.config.settings import get_settings

    settings = get_settings()
    backup_dir = settings.storage_path.parent / "backups"

    if not backup_dir.exists():
        click.echo("No backups directory found")
        return

    backups = sorted(backup_dir.glob("cast2md_backup_*.sql"), reverse=True)

    if not backups:
        click.echo("No backups found")
        return

    click.echo(f"{'Backup File':<45} {'Size':>10} {'Date':<20}")
    click.echo("-" * 75)

    for backup in backups:
        size_mb = backup.stat().st_size / (1024 * 1024)
        mtime = datetime.fromtimestamp(backup.stat().st_mtime)
        click.echo(f"{backup.name:<45} {size_mb:>8.2f} MB {mtime.strftime('%Y-%m-%d %H:%M:%S'):<20}")


@cli.command("reindex-transcripts")
@click.option("--feed-id", "-f", type=int, help="Only reindex transcripts for this feed")
@click.option("--embeddings", "-e", is_flag=True, help="Also regenerate embeddings for semantic search")
def cmd_reindex_transcripts(feed_id: int | None, embeddings: bool):
    """Reindex all transcripts for full-text search.

    Parses transcript markdown files and indexes them into the FTS
    search table. This enables fast full-text search across all transcripts.

    Use --feed-id to limit reindexing to a specific feed.
    Use --embeddings to also regenerate embeddings for semantic search.
    """
    from cast2md.db.connection import get_db, init_db
    from cast2md.db.repository import EpisodeRepository
    from cast2md.search.repository import TranscriptSearchRepository

    init_db()

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        search_repo = TranscriptSearchRepository(conn)

        # Build dict of episode_id -> transcript_path
        cursor = conn.cursor()
        if feed_id:
            # Get episodes for specific feed
            cursor.execute(
                """
                SELECT id, transcript_path FROM episode
                WHERE feed_id = %s AND transcript_path IS NOT NULL AND status = 'completed'
                """,
                (feed_id,),
            )
        else:
            # Get all completed episodes with transcripts
            cursor.execute(
                """
                SELECT id, transcript_path FROM episode
                WHERE transcript_path IS NOT NULL AND status = 'completed'
                """
            )

        episode_transcripts = {row[0]: row[1] for row in cursor.fetchall()}

        if not episode_transcripts:
            click.echo("No transcripts found to index")
            return

        click.echo(f"Found {len(episode_transcripts)} transcripts to index")

        # Reindex FTS
        with click.progressbar(
            episode_transcripts.items(),
            label="Indexing transcripts (FTS)",
            length=len(episode_transcripts),
        ) as items:
            episodes_indexed = 0
            segments_indexed = 0

            for episode_id, transcript_path in items:
                count = search_repo.index_episode(episode_id, transcript_path)
                if count > 0:
                    episodes_indexed += 1
                    segments_indexed += count

    click.echo()
    click.echo(f"Indexed {episodes_indexed} episodes with {segments_indexed} segments")
    click.echo("Full-text search is now available via /api/search/transcripts")

    # Reindex embeddings if requested
    if embeddings:
        click.echo()
        click.echo("Regenerating embeddings for semantic search...")

        with get_db() as conn:
            search_repo = TranscriptSearchRepository(conn)

            with click.progressbar(
                episode_transcripts.items(),
                label="Generating embeddings",
                length=len(episode_transcripts),
            ) as items:
                embedded_count = 0
                for episode_id, transcript_path in items:
                    count = search_repo.index_episode_embeddings(episode_id, transcript_path)
                    if count > 0:
                        embedded_count += 1

        click.echo()
        click.echo(f"Generated embeddings for {embedded_count} episodes")
        click.echo("Semantic search is now available")


@cli.command("reindex-episodes")
def cmd_reindex_episodes():
    """Reindex all episodes for full-text search.

    Rebuilds the episode_fts index from the episode table. This enables
    word-boundary search on episode titles and descriptions.

    Run this after upgrading if search isn't working correctly.
    """
    from cast2md.db.connection import init_db, get_db
    from cast2md.db.repository import EpisodeRepository

    init_db()

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)

        click.echo("Reindexing episodes for full-text search...")
        count = episode_repo.reindex_all_episodes()

    click.echo(f"Indexed {count} episodes")
    click.echo("Episode search now uses word-boundary matching")


@cli.command("backfill-embeddings")
@click.option("--feed-id", "-f", type=int, help="Only backfill embeddings for this feed")
@click.option("--limit", "-n", type=int, help="Maximum number of episodes to process")
def cmd_backfill_embeddings(feed_id: int | None, limit: int | None):
    """Backfill embeddings for episodes missing them.

    Finds completed episodes that have transcripts but no embeddings,
    and generates embeddings for semantic search.

    Use --feed-id to limit to a specific feed.
    Use --limit for testing with a smaller batch.
    """
    from cast2md.db.connection import get_db, init_db
    from cast2md.search.embeddings import is_embeddings_available
    from cast2md.search.repository import TranscriptSearchRepository

    if not is_embeddings_available():
        click.echo("Error: Embeddings not available (sentence-transformers not installed)", err=True)
        raise SystemExit(1)

    init_db()

    with get_db() as conn:
        search_repo = TranscriptSearchRepository(conn)

        # Build query for episodes with transcripts
        cursor = conn.cursor()
        if feed_id:
            cursor.execute(
                """
                SELECT id, transcript_path FROM episode
                WHERE feed_id = %s AND transcript_path IS NOT NULL AND status = 'completed'
                """,
                (feed_id,),
            )
        else:
            cursor.execute(
                """
                SELECT id, transcript_path FROM episode
                WHERE transcript_path IS NOT NULL AND status = 'completed'
                """
            )

        episode_transcripts = {row[0]: row[1] for row in cursor.fetchall()}

        if not episode_transcripts:
            click.echo("No transcripts found to embed")
            return

        # Get episodes that already have embeddings
        embedded_episode_ids = search_repo.get_embedded_episodes()

        # Filter to episodes missing embeddings
        missing = {
            ep_id: path
            for ep_id, path in episode_transcripts.items()
            if ep_id not in embedded_episode_ids
        }

        if not missing:
            click.echo(f"All {len(episode_transcripts)} episodes already have embeddings")
            return

        click.echo(f"Found {len(missing)} episodes missing embeddings (out of {len(episode_transcripts)} total)")

        # Apply limit if specified
        if limit:
            missing = dict(list(missing.items())[:limit])
            click.echo(f"Processing {len(missing)} episodes (limited)")

        # Generate embeddings with progress bar
        with click.progressbar(
            missing.items(),
            label="Generating embeddings",
            length=len(missing),
        ) as items:
            embedded_count = 0
            for episode_id, transcript_path in items:
                try:
                    count = search_repo.index_episode_embeddings(episode_id, transcript_path)
                    if count > 0:
                        embedded_count += 1
                except Exception as e:
                    click.echo(f"\nWarning: Failed to embed episode {episode_id}: {e}", err=True)

    click.echo()
    click.echo(f"Generated embeddings for {embedded_count} episodes")
    click.echo("Semantic search index updated")


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


@cli.command("mcp")
@click.option("--sse", is_flag=True, help="Use SSE/HTTP transport instead of stdio")
@click.option("--host", "-h", default="0.0.0.0", help="Host for SSE server (only with --sse)")
@click.option("--port", "-p", default=8080, help="Port for SSE server (only with --sse)")
def cmd_mcp(sse: bool, host: str, port: int):
    """Start the MCP server for Claude integration.

    By default, runs with stdio transport (for Claude Code/Desktop).
    Use --sse flag for HTTP/SSE transport (for Claude.ai/remote clients).

    Examples:
        cast2md mcp              # stdio mode (Claude Code)
        cast2md mcp --sse        # SSE mode on port 8080
        cast2md mcp --sse -p 9000  # SSE mode on custom port
    """
    from cast2md.mcp.server import run_sse, run_stdio

    if sse:
        click.echo(f"Starting MCP server with SSE transport on http://{host}:{port}/sse")
        run_sse(host=host, port=port)
    else:
        # stdio mode - no output to stdout as it's used for communication
        run_stdio()


# --- Distributed Transcription Node Commands ---


@cli.group()
def node():
    """Manage this machine as a transcriber node."""
    pass


@node.command("register")
@click.option("--server", "-s", required=True, help="URL of the cast2md server")
@click.option("--name", "-n", required=True, help="Name for this node")
def cmd_node_register(server: str, name: str):
    """Register this machine as a transcriber node.

    This stores credentials locally in ~/.cast2md/node.json.

    Example:
        cast2md node register --server http://192.168.1.100:8000 --name "M4 MacBook Pro"
    """
    import httpx

    from cast2md.config.settings import get_settings
    from cast2md.node.config import get_config_path, load_config, save_config, NodeConfig

    # Check if already registered
    existing = load_config()
    if existing:
        click.echo(f"Already registered as '{existing.name}' with server {existing.server_url}")
        if not click.confirm("Re-register with new server?"):
            return

    # Normalize server URL
    if not server.startswith("http"):
        server = f"http://{server}"
    server = server.rstrip("/")

    click.echo(f"Registering with server: {server}")

    # Get whisper config to send
    settings = get_settings()

    try:
        response = httpx.post(
            f"{server}/api/nodes/register",
            json={
                "name": name,
                "url": f"http://localhost:8001",  # Node's local server
                "whisper_model": settings.whisper_model,
                "whisper_backend": settings.whisper_backend,
            },
            timeout=10.0,
        )

        if response.status_code != 200:
            click.echo(f"Error: Registration failed: {response.status_code} - {response.text}", err=True)
            raise SystemExit(1)

        data = response.json()
        node_id = data["node_id"]
        api_key = data["api_key"]

        # Save configuration
        config = NodeConfig(
            server_url=server,
            node_id=node_id,
            api_key=api_key,
            name=name,
        )
        save_config(config)

        click.echo(f"Registered successfully!")
        click.echo(f"  Node ID: {node_id[:8]}...")
        click.echo(f"  Config saved to: {get_config_path()}")
        click.echo()
        click.echo("Start the node with: cast2md node start")

    except httpx.RequestError as e:
        click.echo(f"Error: Could not reach server: {e}", err=True)
        raise SystemExit(1)


@node.command("start")
@click.option("--port", "-p", default=8001, help="Port for node status UI")
@click.option("--no-browser", is_flag=True, help="Don't open browser automatically")
def cmd_node_start(port: int, no_browser: bool):
    """Start the transcriber node worker.

    The node will poll the server for jobs, download audio files,
    transcribe them locally, and upload the results.
    """
    import logging
    import signal
    import sys
    import threading

    from cast2md.node.config import load_config
    from cast2md.node.server import run_server
    from cast2md.node.worker import TranscriberNodeWorker

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    config = load_config()
    if not config:
        click.echo("Error: Node not configured.", err=True)
        click.echo("Run 'cast2md node register --server <url> --name \"Name\"' first.")
        raise SystemExit(1)

    click.echo(f"Starting transcriber node '{config.name}'")
    click.echo(f"Server: {config.server_url}")
    click.echo(f"Status UI: http://localhost:{port}")
    click.echo("Press Ctrl+C to stop")

    # Create worker
    worker = TranscriberNodeWorker(config)

    # Setup signal handlers for graceful shutdown
    def handle_shutdown(signum, frame):
        sig_name = signal.Signals(signum).name
        click.echo(f"\nReceived {sig_name}, shutting down...")
        worker.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    # Start web server in background thread
    server_thread = threading.Thread(
        target=run_server,
        kwargs={"host": "0.0.0.0", "port": port, "worker": worker},
        daemon=True,
    )
    server_thread.start()

    # Give server time to start, then optionally open browser
    if not no_browser:
        time.sleep(0.5)
        webbrowser.open(f"http://localhost:{port}")

    # Run worker (blocking)
    try:
        worker.run()
    except KeyboardInterrupt:
        click.echo("\nShutting down...")
    finally:
        worker.stop()


@node.command("status")
def cmd_node_status():
    """Show node status and configuration."""
    from cast2md.node.config import get_config_path, load_config

    config = load_config()
    if not config:
        click.echo("Node not registered.")
        click.echo(f"Config path: {get_config_path()}")
        click.echo()
        click.echo("Register with: cast2md node register --server <url> --name \"Name\"")
        return

    click.echo("Node Configuration")
    click.echo("=" * 40)
    click.echo(f"Name: {config.name}")
    click.echo(f"Node ID: {config.node_id}")
    click.echo(f"Server: {config.server_url}")
    click.echo(f"Config: {get_config_path()}")

    # Try to check server connectivity
    click.echo()
    click.echo("Server Connection")
    click.echo("-" * 40)

    import httpx
    try:
        response = httpx.post(
            f"{config.server_url}/api/nodes/{config.node_id}/heartbeat",
            headers={"X-Transcriber-Key": config.api_key},
            json={},
            timeout=5.0,
        )
        if response.status_code == 200:
            click.echo("Status: Connected")
        else:
            click.echo(f"Status: Error ({response.status_code})")
    except httpx.RequestError as e:
        click.echo(f"Status: Unreachable ({e})")


@node.command("unregister")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
def cmd_node_unregister(force: bool):
    """Unregister this node and delete local credentials."""
    import httpx

    from cast2md.node.config import delete_config, load_config

    config = load_config()
    if not config:
        click.echo("Node not registered.")
        return

    if not force:
        if not click.confirm(f"Unregister node '{config.name}' from {config.server_url}?"):
            return

    # Try to delete from server
    try:
        response = httpx.delete(
            f"{config.server_url}/api/nodes/{config.node_id}",
            headers={"X-Transcriber-Key": config.api_key},
            timeout=5.0,
        )
        if response.status_code == 200:
            click.echo("Removed from server")
        else:
            click.echo(f"Warning: Could not remove from server ({response.status_code})")
    except httpx.RequestError as e:
        click.echo(f"Warning: Could not reach server: {e}")

    # Delete local config
    if delete_config():
        click.echo("Local credentials deleted")
    else:
        click.echo("No local config found")


if __name__ == "__main__":
    cli()
