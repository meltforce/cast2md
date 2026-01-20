"""Minimal FastAPI server for transcriber node status UI."""

import logging
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from cast2md.node.config import load_config, save_config, NodeConfig
from cast2md.node.worker import TranscriberNodeWorker

logger = logging.getLogger(__name__)

# Available Whisper models
WHISPER_MODELS = [
    ("tiny", "Tiny (~39M params, fastest)"),
    ("base", "Base (~74M params, fast)"),
    ("small", "Small (~244M params)"),
    ("medium", "Medium (~769M params)"),
    ("large-v2", "Large v2 (~1.5B params)"),
    ("large-v3", "Large v3 (~1.5B params)"),
    ("large-v3-turbo", "Large v3 Turbo (~809M params, recommended)"),
]

WHISPER_BACKENDS = [
    ("auto", "Auto (MLX on Apple Silicon, faster-whisper otherwise)"),
    ("mlx", "MLX (Apple Silicon only)"),
    ("faster-whisper", "Faster Whisper (CPU/CUDA)"),
]

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

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request, saved: bool = False):
        """Show node settings."""
        from cast2md.config.settings import get_settings

        config = load_config()
        settings = get_settings()

        # Get system info
        import platform
        import os

        system_info = {
            "platform": platform.system(),
            "machine": platform.machine(),
            "processor": platform.processor() or "Unknown",
            "cpu_count": os.cpu_count(),
        }

        # Try to get memory info
        try:
            import subprocess
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                mem_bytes = int(result.stdout.strip())
                system_info["memory_gb"] = round(mem_bytes / (1024**3), 1)
        except Exception:
            pass

        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "config": config,
                "settings": settings,
                "system_info": system_info,
                "whisper_models": WHISPER_MODELS,
                "whisper_backends": WHISPER_BACKENDS,
                "saved": saved,
            },
        )

    @app.post("/settings")
    async def save_settings(
        request: Request,
        server_url: str = Form(...),
        whisper_model: str = Form(...),
        whisper_backend: str = Form(...),
    ):
        """Save node settings."""
        from cast2md.config.settings import reload_settings

        config = load_config()
        if not config:
            return RedirectResponse(url="/settings", status_code=303)

        # Update node config if server URL changed
        if server_url != config.server_url:
            new_config = NodeConfig(
                server_url=server_url,
                node_id=config.node_id,
                api_key=config.api_key,
                name=config.name,
            )
            save_config(new_config)

        # Save whisper settings to .env file in ~/.cast2md/
        env_path = Path.home() / ".cast2md" / ".env"
        env_lines = []

        # Read existing .env if present
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        key = line.split("=")[0] if "=" in line else ""
                        if key not in ("WHISPER_MODEL", "WHISPER_BACKEND"):
                            env_lines.append(line)

        # Add/update whisper settings
        env_lines.append(f"WHISPER_MODEL={whisper_model}")
        env_lines.append(f"WHISPER_BACKEND={whisper_backend}")

        # Write .env file
        with open(env_path, "w") as f:
            f.write("\n".join(env_lines) + "\n")

        # Reload settings
        reload_settings()

        return RedirectResponse(url="/settings?saved=true", status_code=303)

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
