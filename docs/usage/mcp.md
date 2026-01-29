# MCP Server

cast2md includes a [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server that enables Claude to search and interact with your podcast library.

## Setup

### Claude Code

Add to your Claude Code MCP configuration:

```json
{
  "mcpServers": {
    "podcasts": {
      "command": "cast2md",
      "args": ["mcp"]
    }
  }
}
```

If cast2md is installed in a virtualenv:

```json
{
  "mcpServers": {
    "podcasts": {
      "command": "/path/to/.venv/bin/cast2md",
      "args": ["mcp"]
    }
  }
}
```

### Claude Desktop

Same configuration as Claude Code. Add to `claude_desktop_config.json`.

### Remote / SSE Mode

For remote clients or Claude.ai:

```bash
cast2md mcp --sse --port 8080
```

Connect using the SSE endpoint: `http://localhost:8080/sse`

---

## Available Tools

### search

Universal search for podcast content. Automatically detects podcast/feed mentions, recognizes "latest episode" queries, and searches both episode titles and transcript content.

| Parameter | Type | Description |
|-----------|------|-------------|
| `query` | string | Natural language query |

### semantic_search

Search transcripts using natural language understanding. Combines keyword matching with semantic similarity.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | *(required)* | Search query |
| `feed_id` | int | *(all feeds)* | Limit to specific feed |
| `limit` | int | 20 | Maximum results |
| `mode` | string | `hybrid` | `hybrid`, `semantic`, or `keyword` |

### search_episodes

Search episodes by title and description using full-text search.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | *(required)* | Search query |
| `feed_id` | int | *(all feeds)* | Limit to specific feed |
| `limit` | int | 25 | Maximum results |

### list_feeds

List all podcast feeds in the library. No parameters.

### find_feed

Find a podcast feed by name using fuzzy search.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | string | Podcast name (case-insensitive, partial match) |

### get_feed

Get details for a specific podcast feed with its episodes.

| Parameter | Type | Description |
|-----------|------|-------------|
| `feed_id` | int | Feed ID |

### get_episode

Get details for a specific episode.

| Parameter | Type | Description |
|-----------|------|-------------|
| `episode_id` | int | Episode ID |

### get_transcript

Get transcript text for an episode, optionally around a specific timestamp.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `episode_id` | int | *(required)* | Episode ID |
| `start_time` | float | *(none)* | Start time in seconds |
| `duration` | float | 300 | Duration in seconds to return |

### get_recent_episodes

Get recently published episodes across all feeds.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | int | 7 | Days to look back |
| `limit` | int | 50 | Maximum episodes |

### queue_episode

Queue an episode for download and transcription.

| Parameter | Type | Description |
|-----------|------|-------------|
| `episode_id` | int | Episode ID |

### get_queue_status

Get the current status of the processing queue. No parameters.

### add_feed

Add a new podcast feed by RSS URL.

| Parameter | Type | Description |
|-----------|------|-------------|
| `url` | string | RSS feed URL |

### refresh_feed

Refresh a feed to discover new episodes.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `feed_id` | int | *(required)* | Feed ID |
| `auto_queue` | bool | false | Auto-queue new episodes for processing |

---

## Example Usage with Claude

Once configured, you can ask Claude:

- "What podcasts do I have?"
- "Search for episodes about protein and muscle building"
- "What was discussed in the latest Huberman Lab episode?"
- "Find where cold exposure is mentioned in my podcasts"
- "Add this podcast: https://example.com/feed.xml"
- "How many episodes are in the queue?"
