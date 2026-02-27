"""MCP server setup using FastMCP."""

from mcp.server.fastmcp import FastMCP

# Module-level server instance - will be configured before tools/resources import
mcp: FastMCP = None  # type: ignore


def create_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    skip_db_init: bool = False,
    stateless: bool = False,
) -> FastMCP:
    """Create and configure the MCP server instance.

    Args:
        host: Host to bind to (for SSE mode).
        port: Port to bind to (for SSE mode).
        skip_db_init: Skip database initialization (when mounted inside FastAPI app).
        stateless: Enable stateless HTTP mode (no session tracking).

    Returns:
        Configured FastMCP instance with tools and resources registered.
    """
    global mcp

    mcp = FastMCP(
        name="cast2md",
        instructions="""Podcast transcription service. Search transcripts, manage feeds, and queue episodes for processing.

IMPORTANT: When an episode was mentioned earlier in the conversation, use its ID directly with the cast2md://episodes/{id}/transcript resource instead of searching again. Search results include episode IDs for this purpose.""",
        host=host,
        port=port,
        stateless_http=stateless,
    )

    # Only initialize database in local mode (not when using remote API)
    if not skip_db_init:
        from cast2md.mcp.client import is_remote_mode
        if not is_remote_mode():
            from cast2md.db.connection import init_db
            init_db()

    # Import tools and resources to register them
    # These modules use @mcp.tool() and @mcp.resource() decorators
    from cast2md.mcp import tools  # noqa: F401
    from cast2md.mcp import resources  # noqa: F401

    return mcp


def run_stdio():
    """Run the MCP server with stdio transport."""
    server = create_server()
    server.run(transport="stdio")


def run_sse(host: str = "0.0.0.0", port: int = 8080):
    """Run the MCP server with SSE/HTTP transport.

    Args:
        host: Host to bind to.
        port: Port to bind to.
    """
    server = create_server(host=host, port=port)
    server.run(transport="sse")
