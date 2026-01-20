"""FastAPI application entry point."""

import logging
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info("Starting cast2md...")

    settings = get_settings()
    settings.ensure_directories()

    # Initialize database
    init_db()
    logger.info(f"Database initialized at {settings.database_path}")

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
