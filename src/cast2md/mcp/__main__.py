"""Entry point for running MCP server as a module.

Usage:
    python -m cast2md.mcp           # stdio mode (default)
    python -m cast2md.mcp --sse     # SSE/HTTP mode
    python -m cast2md.mcp --sse --port 9000
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description="cast2md MCP server for Claude integration"
    )
    parser.add_argument(
        "--sse",
        action="store_true",
        help="Use SSE/HTTP transport instead of stdio",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host for SSE server (only with --sse)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for SSE server (only with --sse)",
    )

    args = parser.parse_args()

    from cast2md.mcp.server import run_sse, run_stdio

    if args.sse:
        print(
            f"Starting MCP server with SSE transport on http://{args.host}:{args.port}/sse",
            file=sys.stderr,
        )
        run_sse(host=args.host, port=args.port)
    else:
        run_stdio()


if __name__ == "__main__":
    main()
