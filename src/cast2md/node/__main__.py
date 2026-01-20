"""Run the transcriber node worker."""

import logging
import sys
import threading

from cast2md.node.config import load_config
from cast2md.node.server import run_server
from cast2md.node.worker import TranscriberNodeWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Start the transcriber node."""
    config = load_config()
    if not config:
        print("Error: Node not configured.")
        print("Run 'cast2md node register --server <server_url> --name \"Node Name\"' first.")
        sys.exit(1)

    logger.info(f"Starting transcriber node '{config.name}'")
    logger.info(f"Server: {config.server_url}")

    # Create worker
    worker = TranscriberNodeWorker(config)

    # Start web server in background thread
    server_thread = threading.Thread(
        target=run_server,
        kwargs={"host": "0.0.0.0", "port": 8001, "worker": worker},
        daemon=True,
    )
    server_thread.start()
    logger.info("Started status server on http://0.0.0.0:8001")

    # Run worker (blocking)
    try:
        worker.run()
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down...")
    finally:
        worker.stop()


if __name__ == "__main__":
    main()
