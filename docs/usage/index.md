# Usage

cast2md provides four interfaces for managing podcast transcriptions.

## Interfaces

| Interface | Description | Best For |
|-----------|-------------|----------|
| [Web UI](web-ui.md) | Browser-based management | Day-to-day use, monitoring |
| [CLI](cli.md) | Command-line tool | Automation, scripting, setup |
| [REST API](api.md) | HTTP API | Integration with other tools |
| [MCP Server](mcp.md) | Model Context Protocol | Claude AI integration |

## Common Workflows

### Add a Podcast

=== "Web UI"

    1. Click "Add Feed" on the feeds page
    2. Enter the RSS URL or Apple Podcasts URL
    3. Episodes are discovered automatically

=== "CLI"

    ```bash
    cast2md add-feed "https://example.com/feed.xml"
    # or
    cast2md add-feed "https://podcasts.apple.com/us/podcast/example/id123456"
    ```

=== "API"

    ```bash
    curl -X POST http://localhost:8000/api/feeds \
      -H "Content-Type: application/json" \
      -d '{"url": "https://example.com/feed.xml"}'
    ```

### Get Transcripts

1. **Automatic** -- new episodes are checked for external transcripts on discovery
2. **Manual** -- click "Get Transcript" on individual episodes
3. **Batch** -- click "Get All Transcripts" to process all new episodes in a feed

### Search Transcripts

The search page (`/search`) provides unified search across episode metadata and transcript content:

- **Keyword search** -- PostgreSQL full-text search
- **Semantic search** -- find content by meaning, not just exact words
- **Hybrid mode** -- combines both for best results

### Monitor Processing

The status page (`/status`) shows:

- System health and worker status
- Processing queue with job details
- Remote transcriber node status
- Episode counts by state
