"""FastAPI application entry point."""

import logging
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from cast2md import __version__
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cast2md.api.episodes import router as episodes_router
from cast2md.api.feeds import router as feeds_router
from cast2md.api.itunes import router as itunes_router
from cast2md.api.nodes import router as nodes_router
from cast2md.api.queue import router as queue_router
from cast2md.api.runpod import router as runpod_router
from cast2md.api.search import router as search_router
from cast2md.api.settings import router as settings_router
from cast2md.api.system import router as system_router
from cast2md.config.settings import get_settings
from cast2md.db.connection import close_pool, init_db
from cast2md.scheduler import start_scheduler, stop_scheduler
from cast2md.web.views import configure_templates, router as web_router
from cast2md.worker import get_worker_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def reset_orphaned_jobs():
    """Reset any jobs left in 'running' state from previous server run.

    On server startup, any jobs marked as 'running' are orphaned since
    no workers are actively processing them yet. Reset them to 'queued'
    so they can be picked up by workers.
    """
    from cast2md.db.connection import get_db
    from cast2md.db.repository import JobRepository

    with get_db() as conn:
        job_repo = JobRepository(conn)
        requeued, failed = job_repo.reset_running_jobs()
        if requeued > 0 or failed > 0:
            logger.info(f"Reset orphaned jobs: {requeued} requeued, {failed} failed (max attempts)")


def queue_missing_embeddings():
    """Queue embedding jobs for episodes that have transcripts but no embeddings.

    Runs on startup to backfill embeddings for existing transcripts.
    Uses low priority (10) so these don't interfere with normal operations.
    """
    from cast2md.db.connection import get_db
    from cast2md.db.models import EpisodeStatus, JobType
    from cast2md.db.repository import EpisodeRepository, JobRepository
    from cast2md.search.embeddings import is_embeddings_available
    from cast2md.search.repository import TranscriptSearchRepository

    # Check if embedding infrastructure is available
    if not is_embeddings_available():
        logger.info("Embeddings not available (sentence-transformers not installed), skipping backfill")
        return

    with get_db() as conn:
        episode_repo = EpisodeRepository(conn)
        job_repo = JobRepository(conn)
        search_repo = TranscriptSearchRepository(conn)

        # Get episodes with transcripts (use high limit to get all)
        completed_episodes = episode_repo.get_by_status(EpisodeStatus.COMPLETED, limit=100000)

        # Get episodes that already have embeddings
        embedded_episode_ids = search_repo.get_embedded_episodes()

        # Queue jobs for episodes without embeddings
        queued = 0
        for episode in completed_episodes:
            if episode.id not in embedded_episode_ids and episode.transcript_path:
                # Check if there's already a pending embed job
                if not job_repo.has_pending_job(episode.id, JobType.EMBED):
                    job_repo.create(
                        episode_id=episode.id,
                        job_type=JobType.EMBED,
                        priority=10,  # Low priority for backfill
                    )
                    queued += 1

        if queued > 0:
            logger.info(f"Queued {queued} embedding jobs for backfill")


def setup_signal_handlers():
    """Setup signal handlers for graceful shutdown.

    Ensures SIGTERM/SIGINT trigger FastAPI lifespan shutdown.
    """
    def handle_signal(signum, frame):
        sig_name = signal.Signals(signum).name
        # Use print to ensure message appears before exit
        print(f"Received {sig_name}, initiating shutdown...", flush=True)
        # Raise SystemExit to trigger FastAPI lifespan shutdown
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info("Starting cast2md...")

    # Setup signal handlers for graceful shutdown
    setup_signal_handlers()

    settings = get_settings()
    settings.ensure_directories()

    # Initialize database
    init_db()
    logger.info("Database initialized")

    # Cleanup old trash and orphaned temp files
    from cast2md.storage.filesystem import cleanup_old_trash, cleanup_orphaned_temp_files

    deleted = cleanup_old_trash(days=30)
    if deleted > 0:
        logger.info(f"Cleaned up {deleted} old trash entries")

    deleted = cleanup_orphaned_temp_files(hours=24)
    if deleted > 0:
        logger.info(f"Cleaned up {deleted} orphaned temp files")

    # Cleanup orphaned RunPod nodes (from pods that terminated without notifying server)
    try:
        from cast2md.services.runpod_service import get_runpod_service
        runpod_service = get_runpod_service()
        if runpod_service.is_available():
            deleted = runpod_service.cleanup_orphaned_nodes()
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} orphaned RunPod nodes")
    except Exception as e:
        logger.warning(f"Failed to cleanup orphaned RunPod nodes: {e}")

    # Reset any orphaned jobs from previous run
    reset_orphaned_jobs()

    # Queue embedding jobs for episodes missing embeddings
    queue_missing_embeddings()

    # Start scheduler
    start_scheduler(interval_minutes=60)

    # Start workers
    worker_manager = get_worker_manager()
    worker_manager.start()
    logger.info("Workers started")

    yield

    # Shutdown
    logger.info("Shutting down cast2md...")
    worker_manager.stop()
    logger.info("Workers stopped")
    stop_scheduler()
    close_pool()
    logger.info("Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="cast2md",
    description="Podcast transcription service",
    version=__version__,
    lifespan=lifespan,
)

# Configure templates
templates_path = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_path))
configure_templates(templates)

# Mount static files if directory exists
static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Include routers
app.include_router(feeds_router)
app.include_router(episodes_router)
app.include_router(itunes_router)
app.include_router(nodes_router)
app.include_router(queue_router)
app.include_router(runpod_router)
app.include_router(search_router)
app.include_router(settings_router)
app.include_router(system_router)
app.include_router(web_router)


def run_server(host: str = "0.0.0.0", port: int = 8000, reload: bool = False):
    """Run the server with uvicorn."""
    import uvicorn

    uvicorn.run(
        "cast2md.main:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    run_server()
