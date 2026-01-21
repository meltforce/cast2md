"""FastAPI application entry point."""

import logging
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cast2md.api.episodes import router as episodes_router
from cast2md.api.feeds import router as feeds_router
from cast2md.api.nodes import router as nodes_router
from cast2md.api.queue import router as queue_router
from cast2md.api.search import router as search_router
from cast2md.api.settings import router as settings_router
from cast2md.api.system import router as system_router
from cast2md.config.settings import get_settings
from cast2md.db.connection import init_db
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
    logger.info(f"Database initialized at {settings.database_path}")

    # Cleanup old trash
    from cast2md.storage.filesystem import cleanup_old_trash

    deleted = cleanup_old_trash(days=30)
    if deleted > 0:
        logger.info(f"Cleaned up {deleted} old trash entries")

    # Reset any orphaned jobs from previous run
    reset_orphaned_jobs()

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


# Create FastAPI app
app = FastAPI(
    title="cast2md",
    description="Podcast transcription service",
    version="0.3.0",
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
app.include_router(nodes_router)
app.include_router(queue_router)
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
