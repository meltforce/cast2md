# Development

Guide for developing and contributing to cast2md.

## Dev Environment Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- Docker (for PostgreSQL)
- ffmpeg

### Setup

```bash
# Clone the repository
git clone https://github.com/meltforce/cast2md.git
cd cast2md

# Install dependencies
uv sync

# Start PostgreSQL
docker compose up -d postgres

# Configure
cp .env.example .env
# Edit .env:
#   DATABASE_URL=postgresql://cast2md:dev@localhost:5432/cast2md
#   STORAGE_PATH=./data/podcasts
#   TEMP_DOWNLOAD_PATH=./data/temp

# Initialize database
uv run cast2md init-db

# Start dev server
uv run cast2md serve --reload
```

The dev server runs at `http://localhost:8000` with auto-reload on code changes.

### Database

PostgreSQL runs via Docker Compose. The dev config uses `localhost:5432` (Docker exposes the port to the host), while production uses `postgres:5432` (Docker internal DNS).

```bash
# Start just PostgreSQL
docker compose up -d postgres

# Connect to database
docker compose exec postgres psql -U cast2md cast2md
```

## Testing

```bash
# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/test_search.py

# Run with verbose output
uv run pytest -v
```

## Project Structure

```
src/cast2md/
├── __init__.py
├── main.py              # FastAPI app, lifespan events
├── cli.py               # Click CLI commands
├── api/                  # REST API endpoints
│   ├── episodes.py
│   ├── feeds.py
│   ├── nodes.py
│   ├── queue.py
│   ├── runpod.py
│   ├── search.py
│   ├── settings.py
│   └── system.py
├── config/
│   └── settings.py      # Pydantic settings model
├── constants.py         # Constants shared across packages, imports nothing
├── db/
│   ├── connection.py     # Database connection pool, ping()
│   ├── migrations.py     # Schema migrations
│   ├── models.py         # Data models
│   ├── repository.py     # Database operations
│   ├── schema.py         # SQL schema
│   ├── sql.py            # execute() helper
│   └── tsquery.py        # PostgreSQL tsquery builder, stopword list
├── download/
│   └── downloader.py     # Audio download logic
├── feed/
│   ├── discovery.py      # Episode discovery
│   ├── itunes.py         # iTunes URL resolution
│   └── parser.py         # RSS feed parsing
├── mcp/
│   ├── client.py         # MCP client (remote mode)
│   └── tools.py          # MCP tool definitions
├── node/
│   ├── config.py         # Node credentials
│   ├── server.py         # Node status web UI
│   └── worker.py         # Node transcription worker
├── search/
│   ├── embeddings.py     # Embedding generation
│   ├── parser.py         # Transcript segment parsing
│   └── repository.py     # Search queries (FTS + vector)
├── services/
│   ├── pod_setup.py      # RunPod pod setup
│   └── runpod_service.py # RunPod lifecycle management
├── storage/
│   └── filesystem.py     # File storage, trash management
├── templates/            # Jinja2 HTML templates
├── transcription/
│   ├── formats.py        # VTT/SRT/JSON parsers
│   ├── providers/        # External transcript providers
│   │   ├── base.py
│   │   ├── pocketcasts.py
│   │   └── podcast20.py
│   └── service.py        # Whisper/Parakeet transcription
├── web/
│   └── views.py          # HTML page routes
└── worker/
    └── manager.py        # Background job processing
```

## Code Style

- Formatter/linter: [Ruff](https://docs.astral.sh/ruff/)
- Line length: 100
- Target: Python 3.11+

```bash
# Format code
uv run ruff format

# Check linting
uv run ruff check

# Auto-fix issues
uv run ruff check --fix
```

## Key Patterns

### Database Access

Data access goes through repository classes, and those live in exactly two
places: `db/repository.py` and `search/repository.py`. Endpoint modules — `api/`,
`web/`, `mcp/` and `cli.py` — issue no SQL and open no cursor; they construct a
repository on the connection they already hold.

The one exception is the connectivity probe `SELECT 1`, which asserts that the
database answers rather than reading anything. It has a home of its own in
`db/connection.py:ping()`, so no endpoint needs to write it either.

The rule is checkable, and that is the point of the wording:

```bash
grep -rInE '(SELECT |INSERT INTO |UPDATE .* SET |DELETE FROM )' \
  src/cast2md --include='*.py' | grep -vE '^src/cast2md/(db|search)/'

grep -rn 'cursor.execute\|conn.cursor()' src/cast2md --include='*.py' \
  | grep -vE '^src/cast2md/(db|search)/'
```

Both must come back empty. The second one is needed because the first misses a
statement whose `SET` clause wraps onto the next line — that is how two sites
went unnoticed until 2026-08-07.

### Settings

Settings use Pydantic with environment variable loading. See `config/settings.py` for the full model.

### Background Workers

The `worker/manager.py` module runs background threads for:

- Download workers (configurable concurrency)
- Transcript download workers
- Local transcription worker
- Embedding worker

### Templates

HTML templates use Jinja2 with [Pico CSS](https://picocss.com/) framework. See [UI Guidelines](ui-guidelines.md) for design patterns.

## Documentation

The documentation site uses [MkDocs Material](https://squidfunnel.github.io/mkdocs-material/).

```bash
# Install docs dependencies
uv pip install mkdocs-material

# Serve locally
mkdocs serve

# Build
mkdocs build --strict
```
