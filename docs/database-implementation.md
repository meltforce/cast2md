# Database Implementation

cast2md supports both SQLite and PostgreSQL as database backends. This document describes the dual-database architecture, configuration, and key implementation details.

## Overview

The application was originally designed for SQLite, which works well for single-user deployments. PostgreSQL support was added to eliminate database lock contention and enable concurrent writes for high-throughput scenarios.

| Feature | SQLite | PostgreSQL |
|---------|--------|------------|
| Concurrency | Single writer (WAL mode) | Full concurrent writes |
| Full-text search | FTS5 virtual tables | tsvector + GIN indexes |
| Vector search | sqlite-vec extension | pgvector extension |
| Connection model | Per-request connections | Connection pool |
| Deployment | Zero config, file-based | Requires server |

## Configuration

### Environment Variables

The database is configured via the `DATABASE_URL` environment variable:

```bash
# SQLite (default)
DATABASE_URL=sqlite:///data/cast2md.db

# Or simply use DATABASE_PATH (legacy)
DATABASE_PATH=./data/cast2md.db

# PostgreSQL
DATABASE_URL=postgresql://user:password@localhost:5432/cast2md
```

If `DATABASE_URL` is not set, `DATABASE_PATH` is used with SQLite.

### PostgreSQL Connection Pool

PostgreSQL uses a thread-safe connection pool configured via:

```bash
POOL_MIN_SIZE=1   # Minimum connections (default: 1)
POOL_MAX_SIZE=10  # Maximum connections (default: 10)
```

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

## Schema Differences

### Primary Keys

| SQLite | PostgreSQL |
|--------|------------|
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `SERIAL PRIMARY KEY` |

### Timestamps

| SQLite | PostgreSQL |
|--------|------------|
| `TEXT` (ISO format) | `TIMESTAMP` |
| `datetime('now')` | `NOW()` |

### Full-Text Search

**SQLite (FTS5):**
```sql
CREATE VIRTUAL TABLE transcript_fts USING fts5(
    episode_id, segment_start, segment_end, text
);

-- Query
SELECT * FROM transcript_fts WHERE transcript_fts MATCH 'search term';
```

**PostgreSQL (tsvector):**
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

**SQLite (sqlite-vec):**
```sql
CREATE VIRTUAL TABLE segment_vec USING vec0(
    embedding FLOAT[384],
    +episode_id INTEGER,
    +feed_id INTEGER,
    +segment_start FLOAT,
    +segment_end FLOAT,
    +text_hash TEXT,
    +model_name TEXT
);

-- Query (KNN syntax)
SELECT * FROM segment_vec
WHERE embedding MATCH ? AND k = 20;
```

**PostgreSQL (pgvector):**
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
├── config.py           # Database type detection and settings
├── connection.py       # Connection management (SQLite/PostgreSQL)
├── schema.py           # SQLite schema (FTS5, sqlite-vec)
├── schema_postgres.py  # PostgreSQL schema (tsvector, pgvector)
├── migrations.py       # SQLite migrations
├── migrations_postgres.py  # PostgreSQL migrations
└── repository.py       # Data access layer (database-agnostic)
```

### Database Detection

```python
from cast2md.db.config import get_db_config

config = get_db_config()
if config.is_postgresql:
    # PostgreSQL-specific code
else:
    # SQLite-specific code
```

### SQL Placeholder Helper

```python
from cast2md.db.config import get_placeholder

# Returns '?' for SQLite, '%s' for PostgreSQL
placeholder = get_placeholder()

cursor.execute(
    f"SELECT * FROM episode WHERE id = {placeholder}",
    (episode_id,)
)
```

### Connection Patterns

**psycopg2 (PostgreSQL) requires cursor:**
```python
# PostgreSQL - must use cursor
cursor = conn.cursor()
cursor.execute("SELECT * FROM feed WHERE id = %s", (feed_id,))
row = cursor.fetchone()
```

**SQLite allows direct connection execution:**
```python
# SQLite - can use connection directly
cursor = conn.execute("SELECT * FROM feed WHERE id = ?", (feed_id,))
row = cursor.fetchone()
```

### Embedding Format

Embeddings have different storage formats:

**SQLite (binary-packed float32):**
```python
import struct

# Generate embedding
embedding_array = model.encode(text)

# Pack as binary for sqlite-vec
embedding_bytes = struct.pack(f"{len(embedding_array)}f", *embedding_array)
```

**PostgreSQL (numpy array):**
```python
import numpy as np

# Generate embedding
embedding_array = model.encode(text, convert_to_numpy=True)

# pgvector accepts numpy arrays directly
# No conversion needed
```

The `generate_embeddings_batch` function handles this:

```python
from cast2md.search.embeddings import generate_embeddings_batch

# as_numpy=True for PostgreSQL, False for SQLite
embeddings = generate_embeddings_batch(texts, model_name, as_numpy=config.is_postgresql)
```

## Migration from SQLite

To migrate an existing SQLite database to PostgreSQL:

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

## Rollback

To revert to SQLite:

1. Remove or rename `DATABASE_URL` from environment
2. Set `DATABASE_PATH` to your SQLite database file
3. Restart the application

Both database code paths remain functional.

## Performance Considerations

### SQLite

- **Pros:** Zero configuration, embedded, fast for reads
- **Cons:** Single writer lock, database lock errors under load
- **Best for:** Single-user or low-write-volume deployments

### PostgreSQL

- **Pros:** Full concurrent writes, connection pooling, scalable
- **Cons:** Requires separate server, more setup
- **Best for:** Multi-worker deployments, high throughput

### Vector Search Performance

| Operation | sqlite-vec | pgvector (HNSW) |
|-----------|------------|-----------------|
| Index type | Flat | HNSW (approximate) |
| Insert | Fast | Moderate |
| Search (10k vectors) | ~450ms | ~50ms |
| Search (100k vectors) | ~4s | ~100ms |

pgvector with HNSW indexes provides significantly faster approximate nearest neighbor search at scale.

## Troubleshooting

### "database is locked" (SQLite)

This occurs when multiple writers contend for the database. Solutions:

1. Reduce concurrent workers
2. Increase `PRAGMA busy_timeout`
3. **Migrate to PostgreSQL** (recommended for high-throughput)

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

**SQLite (default):**
```toml
# No additional dependencies, uses Python's built-in sqlite3
# Optional for vector search:
"sqlite-vec>=0.1.0"
```

**PostgreSQL:**
```toml
"psycopg2-binary>=2.9.0"
"pgvector>=0.2.0"
```

Install PostgreSQL support:

```bash
pip install cast2md[postgres]
# or
uv sync --extra postgres
```
