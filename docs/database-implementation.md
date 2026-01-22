# Database Implementation

cast2md requires PostgreSQL as the database backend. This document describes the PostgreSQL configuration, architecture, and key implementation details.

## Overview

The application was originally designed for SQLite, but migrated to PostgreSQL to eliminate database lock contention and enable concurrent writes for high-throughput scenarios. PostgreSQL is now required.

### Key Features

| Feature | Implementation |
|---------|----------------|
| Concurrency | Full concurrent writes via connection pool |
| Full-text search | tsvector + GIN indexes |
| Vector search | pgvector extension with HNSW indexes |
| Connection model | Thread-safe connection pool |
| Backup/Restore | `pg_dump` and `psql` |

### Why PostgreSQL?

**SQLite limitations encountered:**
- "database is locked" errors under concurrent load
- Single writer bottleneck (even with WAL mode)
- Erratic worker counts (3/10 instead of 10/10 due to lock contention)
- Required reducing workers from 10 to 2 to avoid lock errors

**PostgreSQL benefits:**
- Zero lock errors with 10 parallel workers
- ~10 transcripts/second throughput
- API remains responsive during heavy background processing
- Proper concurrent write support

See "Performance Considerations" section for benchmark results.

## Configuration

### Environment Variables

The database is configured via the `DATABASE_URL` environment variable:

```bash
# PostgreSQL (required)
DATABASE_URL=postgresql://user:password@localhost:5432/cast2md
```

### Connection Pool

PostgreSQL uses a thread-safe connection pool configured via:

```bash
POOL_MIN_SIZE=1   # Minimum connections (default: 1)
POOL_MAX_SIZE=10  # Maximum connections (default: 10)
```

The connection pool enables concurrent writes and eliminates lock contention.

## Docker Setup

For PostgreSQL with pgvector, use the official pgvector image:

```yaml
# docker-compose.yml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: cast2md
      POSTGRES_USER: cast2md
      POSTGRES_PASSWORD: yourpassword
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U cast2md"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  postgres_data:
```

Start PostgreSQL:

```bash
docker compose up -d postgres
```

## Backup and Restore

### Manual Backup

Create a SQL dump of the database:

```bash
cast2md backup -o /path/to/backup.sql
```

This uses `pg_dump` internally and requires the PostgreSQL client tools to be installed.

### Manual Restore

Restore from a SQL backup:

```bash
cast2md restore /path/to/backup.sql
```

This uses `psql` internally and:
- Creates a pre-restore backup automatically for safety
- Requires confirmation before proceeding
- Requires PostgreSQL client tools to be installed

### Automated Backup

Use the provided backup script with cron:

```bash
# Add to crontab: backup every 6 hours
0 */6 * * * /opt/cast2md/deploy/backup.sh
```

The script:
- Saves backups to `/mnt/nas/cast2md/backups/` (or configured directory)
- Uses filename format: `cast2md_backup_YYYYMMDD_HHMMSS.sql`
- Retains last 7 days of backups automatically

### List Backups

View available backups:

```bash
cast2md list-backups
```

## Schema Details

### Primary Keys

Uses PostgreSQL `SERIAL PRIMARY KEY` for auto-incrementing primary keys:

```sql
CREATE TABLE feed (
    id SERIAL PRIMARY KEY,
    ...
);
```

### Timestamps

Uses PostgreSQL `TIMESTAMP` type with `NOW()` for current timestamp:

```sql
CREATE TABLE episode (
    ...
    created_at TIMESTAMP DEFAULT NOW(),
    published_at TIMESTAMP
);
```

### Full-Text Search

Uses PostgreSQL tsvector with GIN indexes:
```sql
CREATE TABLE transcript_segments (
    id SERIAL PRIMARY KEY,
    episode_id INTEGER NOT NULL,
    segment_start REAL NOT NULL,
    segment_end REAL NOT NULL,
    text TEXT NOT NULL,
    text_search TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);
CREATE INDEX idx_transcript_search ON transcript_segments USING GIN (text_search);

-- Query
SELECT * FROM transcript_segments
WHERE text_search @@ plainto_tsquery('english', 'search term');
```

### Vector Search

Uses PostgreSQL pgvector extension with HNSW indexes for fast approximate nearest neighbor search:
```sql
CREATE TABLE segment_embeddings (
    id SERIAL PRIMARY KEY,
    episode_id INTEGER NOT NULL,
    feed_id INTEGER NOT NULL,
    segment_start REAL NOT NULL,
    segment_end REAL NOT NULL,
    text_hash TEXT NOT NULL,
    model_name TEXT NOT NULL,
    embedding vector(384),
    UNIQUE(episode_id, segment_start, segment_end)
);
CREATE INDEX idx_segment_embedding_vec ON segment_embeddings
    USING hnsw (embedding vector_cosine_ops);

-- Query (cosine distance)
SELECT * FROM segment_embeddings
ORDER BY embedding <=> $1
LIMIT 20;
```

## Code Architecture

### Key Files

```
src/cast2md/db/
├── config.py              # Database configuration
├── connection.py          # Connection pool management
├── schema_postgres.py     # PostgreSQL schema (tsvector, pgvector)
├── migrations_postgres.py # PostgreSQL migrations
└── repository.py          # Data access layer
```

### Connection Patterns

PostgreSQL uses psycopg2 which requires explicit cursor creation:

```python
from cast2md.db.connection import get_db

with get_db() as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM feed WHERE id = %s", (feed_id,))
    row = cursor.fetchone()
```

### SQL Placeholders

PostgreSQL uses `%s` for parameter placeholders:

```python
cursor.execute(
    "SELECT * FROM episode WHERE id = %s",
    (episode_id,)
)
```

### Embedding Format

Embeddings are stored as numpy arrays for pgvector:

```python
import numpy as np
from cast2md.search.embeddings import generate_embeddings_batch

# Generate embeddings as numpy arrays for pgvector
embeddings = generate_embeddings_batch(texts, model_name, as_numpy=True)

# pgvector accepts numpy arrays directly
cursor.execute(
    "INSERT INTO segment_embeddings (embedding, ...) VALUES (%s, ...)",
    (embeddings[0], ...)
)
```

## Migration from SQLite (Historical)

For legacy deployments migrating from SQLite to PostgreSQL:

1. **Start PostgreSQL container:**
   ```bash
   docker compose up -d postgres
   ```

2. **Update environment:**
   ```bash
   export DATABASE_URL=postgresql://cast2md:password@localhost:5432/cast2md
   ```

3. **Run migration script:**
   ```bash
   python scripts/migrate_to_postgres.py
   ```

4. **Restart application:**
   ```bash
   systemctl restart cast2md
   ```

### Migration Notes

- Schema version is tracked in `schema_version` table
- PostgreSQL starts at schema version 10 (latest)
- SQLite database is preserved as backup
- Embeddings are regenerated (different storage format)

**Note:** New deployments should use PostgreSQL from the start. SQLite support is maintained for legacy compatibility only.

## Performance Considerations

PostgreSQL enables high-throughput concurrent processing:

- **Concurrency:** Full concurrent writes via connection pool
- **No lock contention:** Zero "database is locked" errors
- **Scalability:** Connection pooling allows 10+ parallel workers
- **API responsiveness:** REST API remains responsive during heavy background jobs

### Real-World Benchmark (January 2026)

Test configuration: 10 transcript download workers, PostgreSQL with pgvector.

| Feed | Episodes | Processing Time | Transcript Source |
|------|----------|-----------------|-------------------|
| Podcasting 2.0 | 180 | ~40 seconds | `podcast2.0:srt` (native) |
| Acquired | 212 | ~25 seconds | `pocketcasts` (auto-generated) |
| Lex Fridman | 490 | ~50 seconds | Mixed |

**Key findings:**

- **Throughput:** ~10 transcripts/second with 10 parallel workers
- **Zero lock errors:** No "database is locked" errors during high-throughput processing
- **Stable worker count:** Workers consistently reported as 10/10 running (SQLite would show erratic counts like 3/10 due to lock contention)
- **API responsiveness:** REST API remained responsive during heavy background processing

With SQLite, the same workload caused frequent "database is locked" errors and required limiting workers to 2. PostgreSQL eliminated this bottleneck entirely.

### Vector Search Performance

pgvector with HNSW indexes provides fast approximate nearest neighbor search:

| Operation | pgvector (HNSW) |
|-----------|-----------------|
| Index type | HNSW (approximate) |
| Search (10k vectors) | ~50ms |
| Search (100k vectors) | ~100ms |

HNSW (Hierarchical Navigable Small World) indexes enable sub-linear search time as the dataset grows.

## Troubleshooting

### psycopg2 connection errors

Ensure the PostgreSQL server is running and accessible:

```bash
# Check connection
psql -h localhost -U cast2md -d cast2md -c "SELECT 1"
```

### pgvector extension not available

The pgvector extension must be enabled:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

The `pgvector/pgvector:pg16` Docker image includes it pre-installed.

### Embedding insertion failures

If embeddings fail with type errors:

- Ensure `pgvector` Python package is installed
- Verify embeddings are numpy arrays (not binary bytes)
- Check that `register_vector(conn)` was called

## Dependencies

PostgreSQL requires the following Python packages:

```toml
"psycopg2-binary>=2.9.0"  # PostgreSQL adapter
"pgvector>=0.2.0"         # Vector type support
```

Install dependencies:

```bash
pip install cast2md
# or
uv sync
```

PostgreSQL client tools (`pg_dump`, `psql`) are required for backup/restore commands:

```bash
# Ubuntu/Debian
sudo apt-get install postgresql-client

# macOS
brew install postgresql
```
