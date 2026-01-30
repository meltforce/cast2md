# Usage

cast2md provides a web interface for managing podcast feeds and searching transcripts.

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

The [search page](search.md) (`/search`) provides unified search across episode metadata and transcript content:

- **Keyword search** -- PostgreSQL full-text search
- **Semantic search** -- find content by meaning, not just exact words
- **Hybrid mode** -- combines both for best results

## Sections

| Page | Description |
|------|-------------|
| [Search](search.md) | Unified search across episodes and transcripts |
| [Feeds](feeds.md) | Feed management, episode actions, and feed deletion |
| [Chat about Podcasts](chat.md) | Use Claude or another LLM to chat about your podcast library |
