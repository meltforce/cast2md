# Semantic Search Implementation Plan

**Status: ✅ COMPLETED (2026-01-21)**

## Goal
Add vector/semantic search to cast2md so queries like "protein and strength" find conceptually related content (muscle, weightlifting, nutrition) across diverse podcast topics. Must work well for both web UI and LLM/MCP use.

## Approach: Hybrid Search
Combine existing FTS5 keyword search with new vector similarity search using Reciprocal Rank Fusion (RRF). This gives:
- **Keyword precision** - exact phrase matching when needed
- **Semantic understanding** - conceptual queries work naturally

## Technical Stack
- **Embeddings**: `sentence-transformers` with `all-MiniLM-L6-v2` (384-dim, ~80MB, runs on CPU)
- **Vector storage**: `sqlite-vec` extension with `vec0` virtual table for indexed KNN search
- **Fusion**: RRF algorithm to combine keyword and vector results

---

## Implementation Steps

### Phase 1: Dependencies & Schema

**1. Add dependencies to `pyproject.toml`:**
```toml
"sentence-transformers>=2.2.0",
"sqlite-vec>=0.1.0",
```

**2. Add migration (version 7) to `src/cast2md/db/migrations.py`:**
```sql
CREATE TABLE segment_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
    segment_start REAL NOT NULL,
    segment_end REAL NOT NULL,
    text_hash TEXT NOT NULL,
    embedding BLOB NOT NULL,
    model_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(episode_id, segment_start, segment_end)
);
```

**3. Load sqlite-vec extension in `src/cast2md/db/connection.py`**

### Phase 2: Embedding Generation

**4. Create `src/cast2md/search/embeddings.py`:**
- Lazy-load sentence-transformers model (singleton)
- `generate_embedding(text) -> bytes` - single text
- `generate_embeddings_batch(texts) -> list[bytes]` - batch processing
- `text_hash(text) -> str` - for change detection

**5. Modify `TranscriptSearchRepository.index_episode()` in `src/cast2md/search/repository.py`:**
- After FTS indexing, generate embeddings for segments
- Store in `segment_embeddings` table
- Use batch processing for efficiency

### Phase 3: Hybrid Search

**6. Add `hybrid_search()` method to `TranscriptSearchRepository`:**
```python
def hybrid_search(
    query: str,
    feed_id: int | None = None,
    limit: int = 20,
    mode: Literal["hybrid", "semantic", "keyword"] = "hybrid",
) -> HybridSearchResponse:
```
- Get FTS5 results (keyword)
- Generate query embedding, get vector similarity results
- Combine with RRF: `score = sum(1 / (60 + rank))`

**7. Add vector search helper `_vector_search()`:**
```sql
SELECT se.*, vec_distance_cosine(embedding, ?) as distance
FROM segment_embeddings se
JOIN episode e ON se.episode_id = e.id
ORDER BY distance ASC
```

### Phase 4: API & MCP

**8. Add endpoint in `src/cast2md/api/search.py`:**
```python
@router.get("/semantic")
def semantic_search(
    q: str,
    feed_id: int | None = None,
    limit: int = 20,
    mode: Literal["hybrid", "semantic", "keyword"] = "hybrid",
)
```

**9. Add MCP tool in `src/cast2md/mcp/tools.py`:**
```python
@mcp.tool()
def semantic_search(
    query: str,
    feed_id: int | None = None,
    limit: int = 20,
    mode: str = "hybrid",
) -> dict:
    """Search transcripts using natural language understanding."""
```

### Phase 5: Migration & Background Processing

**10. Add `EMBED` job type to `src/cast2md/db/models.py`**

**11. Add embedding worker to `src/cast2md/worker/manager.py`:**
- Process `EMBED` jobs at low priority
- Batch embed segments for efficiency

**12. Queue embedding jobs for existing episodes on startup in `src/cast2md/main.py`**

---

## Files to Modify

| File | Changes |
|------|---------|
| `pyproject.toml` | Add sentence-transformers, sqlite-vec |
| `src/cast2md/db/migrations.py` | Add version 7 migration |
| `src/cast2md/db/connection.py` | Load sqlite-vec extension |
| `src/cast2md/db/models.py` | Add `EMBED` to JobType |
| `src/cast2md/search/repository.py` | Add hybrid_search, modify index_episode |
| `src/cast2md/api/search.py` | Add /semantic endpoint |
| `src/cast2md/mcp/tools.py` | Add semantic_search tool |
| `src/cast2md/worker/manager.py` | Add embedding worker |
| `src/cast2md/main.py` | Queue embedding jobs on startup |

## New Files

| File | Purpose |
|------|---------|
| `src/cast2md/search/embeddings.py` | Embedding generation module |

---

## Verification

1. **Unit test**: Embedding generation produces 384-dim vectors
2. **Integration test**: hybrid_search returns results for "protein strength" that include "muscle", "weightlifting"
3. **MCP test**: `semantic_search` tool works from Claude Desktop
4. **Migration test**: Existing transcripts get embeddings via background job

## Example Usage

```python
# API
GET /api/search/semantic?q=protein+and+strength&mode=hybrid

# MCP
semantic_search("discussions about building muscle", mode="hybrid")
# Returns: episodes about weightlifting, nutrition, fitness
# Even if they never say "muscle" explicitly
```

---

## Implementation Notes (2026-01-21)

### Database Schema Changes

**Migration v7**: Created `segment_embeddings` table (later replaced)

**Migration v8**: Migrated to `vec0` virtual table for indexed KNN search:
```sql
CREATE VIRTUAL TABLE segment_vec USING vec0(
    embedding float[384],
    +episode_id INTEGER,
    +feed_id INTEGER,
    +segment_start FLOAT,
    +segment_end FLOAT,
    +text_hash TEXT,
    +model_name TEXT
)
```

### sqlite-vec 0.1.6 Quirks

Several issues discovered during implementation:

1. **REAL type not supported**: Auxiliary columns with `REAL` type cause a misleading "chunk_size must be a non-zero positive integer" error. Use `FLOAT` instead.

2. **Strict type matching**: Values inserted into FLOAT columns must be Python floats. Integer values (even `46.0` stored as int) cause type mismatch errors.

3. **Auxiliary column syntax**: All auxiliary columns require explicit type declarations (`+column_name TYPE`).

### Performance Characteristics

| Operation | Time |
|-----------|------|
| Keyword search (FTS5) | ~4ms |
| Query embedding | ~15ms (after model loaded) |
| Vector KNN search | ~450ms |
| Model loading (first query) | ~2 seconds |

### Key Differences from Original Plan

1. **vec0 virtual table** instead of regular table - required for indexed O(log n) KNN search
2. **feed_id stored in vec0** - enables filtering without JOIN during KNN
3. **Metadata lookup after KNN** - vec0 returns only indexed columns, metadata fetched separately
4. **Embeddings persist** - stored in SQLite, survive restarts (only model needs reload)
