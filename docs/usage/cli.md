# CLI Reference

cast2md provides a command-line interface built with [Click](https://click.palletsprojects.com/).

## Usage

```bash
cast2md [OPTIONS] COMMAND [ARGS]
```

**Global Options:**

| Option | Description |
|--------|-------------|
| `--version` | Show version and exit |
| `--help` | Show help and exit |

---

## Feed Management

### add-feed

Add a new podcast feed.

```bash
cast2md add-feed URL
```

Accepts RSS feed URLs or Apple Podcasts URLs (automatically resolved to RSS).

```bash
cast2md add-feed "https://example.com/feed.xml"
cast2md add-feed "https://podcasts.apple.com/us/podcast/example/id123456"
```

### list-feeds

List all podcast feeds.

```bash
cast2md list-feeds
```

### poll

Poll a feed for new episodes.

```bash
cast2md poll FEED_ID
```

| Argument | Description |
|----------|-------------|
| `FEED_ID` | Numeric ID of the feed |

---

## Episode Management

### list-episodes

List episodes for a feed.

```bash
cast2md list-episodes FEED_ID [OPTIONS]
```

| Argument | Description |
|----------|-------------|
| `FEED_ID` | Numeric ID of the feed |

| Option | Default | Description |
|--------|---------|-------------|
| `-n`, `--limit` | 20 | Maximum episodes to show |

### download

Download an episode's audio file.

```bash
cast2md download EPISODE_ID
```

### transcribe

Transcribe an episode's audio. The episode must be downloaded first.

```bash
cast2md transcribe EPISODE_ID [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-t`, `--timestamps` | Include timestamps in output |

### process

Download and transcribe an episode (combines `download` + `transcribe`).

```bash
cast2md process EPISODE_ID [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-t`, `--timestamps` | Include timestamps in output |

---

## Server

### serve

Start the web server.

```bash
cast2md serve [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `-h`, `--host` | `0.0.0.0` | Host to bind to |
| `-p`, `--port` | `8000` | Port to bind to |
| `-r`, `--reload` | off | Enable auto-reload for development |

```bash
# Production
cast2md serve

# Development
cast2md serve --reload

# Custom host/port
cast2md serve --host localhost --port 8080
```

### status

Show system status and statistics.

```bash
cast2md status
```

### mcp

Start the MCP server for Claude integration.

```bash
cast2md mcp [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--sse` | off | Use SSE/HTTP transport instead of stdio |
| `-h`, `--host` | `0.0.0.0` | Host for SSE server (only with `--sse`) |
| `-p`, `--port` | `8080` | Port for SSE server (only with `--sse`) |

```bash
# stdio mode (for Claude Code/Desktop)
cast2md mcp

# SSE mode (for remote clients)
cast2md mcp --sse --port 9000
```

---

## Database

### init-db

Initialize the database schema.

```bash
cast2md init-db
```

### backup

Create a database backup using pg_dump.

```bash
cast2md backup [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-o`, `--output` | Custom output path (default: `data/backups/` with timestamp) |

```bash
cast2md backup
cast2md backup -o /tmp/backup.sql
```

### restore

Restore database from a backup using psql.

```bash
cast2md restore BACKUP_FILE [OPTIONS]
```

!!! warning
    This overwrites the current database.

| Option | Description |
|--------|-------------|
| `-f`, `--force` | Skip confirmation prompt |

```bash
cast2md restore backup.sql
cast2md restore backup.sql --force
```

### list-backups

List available database backups.

```bash
cast2md list-backups
```

---

## Search & Indexing

### reindex-transcripts

Reindex all transcripts for full-text search.

```bash
cast2md reindex-transcripts [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-f`, `--feed-id` | Only reindex transcripts for this feed |
| `-e`, `--embeddings` | Also regenerate embeddings for semantic search |

```bash
# Reindex FTS only
cast2md reindex-transcripts

# Reindex FTS and embeddings
cast2md reindex-transcripts --embeddings

# Reindex single feed
cast2md reindex-transcripts --feed-id 1
```

### reindex-episodes

Rebuild the episode full-text search index.

```bash
cast2md reindex-episodes
```

### backfill-embeddings

Generate embeddings for episodes that are missing them.

```bash
cast2md backfill-embeddings [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-f`, `--feed-id` | Only backfill for this feed |
| `-n`, `--limit` | Maximum number of episodes to process |

```bash
cast2md backfill-embeddings
cast2md backfill-embeddings --feed-id 1 --limit 10
```

---

## Node Management

Commands for managing this machine as a transcriber node. See [Distributed Transcription](../distributed/setup.md) for the full setup guide.

### node register

Register this machine as a transcriber node.

```bash
cast2md node register --server URL --name NAME
```

| Option | Description |
|--------|-------------|
| `-s`, `--server` | URL of the cast2md server (required) |
| `-n`, `--name` | Name for this node (required) |

Credentials are saved to `~/.cast2md/node.json`.

### node start

Start the transcriber node worker.

```bash
cast2md node start [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `-p`, `--port` | `8001` | Port for node status UI |
| `--no-browser` | off | Don't open browser automatically |

### node status

Show node status and configuration.

```bash
cast2md node status
```

### node unregister

Unregister this node and delete local credentials.

```bash
cast2md node unregister [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-f`, `--force` | Skip confirmation |
