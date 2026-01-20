"""Minimal FastAPI server for transcriber node status UI."""

import logging
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from cast2md.node.config import load_config
from cast2md.node.worker import TranscriberNodeWorker

logger = logging.getLogger(__name__)

# Templates path
templates_path = Path(__file__).parent / "templates"


def create_app(worker: Optional[TranscriberNodeWorker] = None) -> FastAPI:
    """Create the FastAPI app for the node UI.

    Args:
        worker: Optional worker instance to show status from.

    Returns:
        FastAPI application.
    """
    app = FastAPI(
        title="cast2md Transcriber Node",
        description="Remote transcription node status",
        version="0.1.0",
    )

    templates = Jinja2Templates(directory=str(templates_path))

    # Store worker reference
    app.state.worker = worker

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        """Show node status page."""
        config = load_config()
        worker = request.app.state.worker

        current_job = None
        is_running = False

        if worker:
            is_running = worker.is_running
            current_job = worker.current_job

        return templates.TemplateResponse(
            "status.html",
            {
                "request": request,
                "config": config,
                "is_running": is_running,
                "current_job": current_job,
            },
        )

    @app.get("/status")
    async def status():
        """Status endpoint for server connectivity tests."""
        config = load_config()
        worker = app.state.worker

        return {
            "status": "ok",
            "name": config.name if config else "unknown",
            "running": worker.is_running if worker else False,
            "current_job": worker.current_job if worker else None,
        }

    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "healthy"}

    @app.get("/queue", response_class=HTMLResponse)
    async def queue_page(request: Request):
        """Show queue status from main server."""
        config = load_config()
        queue_data = None
        error = None

        if config:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{config.server_url}/api/queue/status",
                        headers={"X-Transcriber-Key": config.api_key},
                        timeout=10.0,
                    )
                    if response.status_code == 200:
                        queue_data = response.json()
                    else:
                        error = f"Server returned {response.status_code}"
            except Exception as e:
                error = str(e)

        return templates.TemplateResponse(
            "queue.html",
            {
                "request": request,
                "config": config,
                "queue": queue_data,
                "error": error,
            },
        )

    return app


def run_server(host: str = "0.0.0.0", port: int = 8001, worker: Optional[TranscriberNodeWorker] = None):
    """Run the node status server.

    Args:
        host: Host to bind to.
        port: Port to bind to.
        worker: Worker instance to show status from.
    """
    import uvicorn

    app = create_app(worker)
    uvicorn.run(app, host=host, port=port)
