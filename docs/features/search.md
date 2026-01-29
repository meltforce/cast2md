# Search

cast2md provides hybrid search across episode metadata and transcript content, combining full-text keyword matching with semantic (meaning-based) search.

## Overview

The search page at `/search` provides a unified interface. Behind the scenes, three search systems work together:

```
Query → Generate embedding (~20ms)
     │
     ├── Episode title/description FTS
     ├── Transcript segment tsvector search
     └── pgvector HNSW similarity search
     │
     Reciprocal Rank Fusion → Combined results
```

## Search Modes

| Mode | Description | Best For |
|------|-------------|----------|
| **Hybrid** (default) | Combines keyword and semantic search | General use |
| **Keyword** | PostgreSQL full-text search only | Exact terms, names, numbers |
| **Semantic** | Vector similarity search only | Conceptual queries, multilingual |

## Result Types

Search returns two categories of results:

### Episode Matches

- Matched episode title or description
- Shows "title" badge
- Links directly to episode (no timestamp)

### Transcript Matches

- Matched content within transcripts
- Shows badge: "keyword", "semantic", or "both"
- Links to episode at the matched timestamp

---

## How It Works

### Full-Text Search (Keyword)

Uses PostgreSQL `tsvector` and `tsquery` for fast keyword matching:

- Episode titles and descriptions are indexed in `episode_search` table
- Transcript segments are indexed with tsvector columns
- Supports stemming, ranking, and phrase matching

### Semantic Search

Uses sentence-transformers embeddings stored in pgvector:

- **Model**: `paraphrase-multilingual-MiniLM-L12-v2`
- **Dimensions**: 384
- **Languages**: 50+ including German
- **Index**: HNSW (Hierarchical Navigable Small World) for fast approximate nearest neighbor
- **Distance**: Cosine similarity (`<=>` operator)

The model understands semantic similarity -- for example, searching for "kaltbaden" (cold bathing) also finds content about "eisbaden" (ice bathing).

### Reciprocal Rank Fusion (RRF)

In hybrid mode, results from keyword and semantic search are combined using RRF:

1. Each search method produces a ranked list
2. RRF assigns scores based on rank position: `1 / (k + rank)`
3. Scores are summed across methods
4. Results are re-ranked by combined score

This balances precision (keyword) with recall (semantic).

---

## Segment Merging

Transcripts can have word-level timestamps where each word is a separate segment. The system automatically merges these into phrases during indexing:

- **Phrase boundaries**: punctuation, pauses (>1.5s), or max 200 characters
- **Applies to**: both FTS indexing and embedding generation
- **Improves**: search quality (fewer noisy single-word results) and readability

---

## Embedding Generation

Embeddings are generated in the background:

1. When a new transcript is saved, an `EMBED` job is queued
2. The embedding worker processes these jobs
3. Transcript segments are merged into phrases, then embedded
4. Embeddings are stored in PostgreSQL with pgvector

### Startup Behavior

- **Embeddings**: persisted in PostgreSQL (survive restarts)
- **Model loading**: ~3 seconds on first semantic query after restart

### Reindexing

```bash
# Reindex FTS only
cast2md reindex-transcripts

# Reindex FTS and regenerate all embeddings
cast2md reindex-transcripts --embeddings

# Backfill missing embeddings only
cast2md backfill-embeddings
```

---

## API

### Search Endpoint

```
GET /api/search/semantic?q={query}&mode={mode}&limit={limit}&feed_id={feed_id}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `q` | *(required)* | Search query |
| `mode` | `hybrid` | `hybrid`, `semantic`, or `keyword` |
| `limit` | `20` | Maximum results |
| `feed_id` | *(all)* | Limit to specific feed |

### Embedding Stats

```
GET /api/search/semantic/stats
```

Returns count of episodes with/without embeddings.

### MCP Tool

```python
semantic_search(query="protein and muscle building", mode="hybrid")
```

---

## Embedding Model

The default model `paraphrase-multilingual-MiniLM-L12-v2` was chosen for:

- **Multilingual support** -- 50+ languages including German
- **Small size** -- ~470 MB, 384 dimensions
- **Good quality** -- strong semantic understanding across languages
- **Fast inference** -- ~20ms per query on CPU

---

## pgvector Configuration

- **Extension**: `vector` (installed automatically)
- **Column**: `vector(384)` matching embedding dimension
- **Index**: HNSW for fast approximate nearest neighbor search
- **Distance metric**: Cosine distance

---

## Key Files

| File | Purpose |
|------|---------|
| `search/embeddings.py` | Embedding generation, model config |
| `search/parser.py` | Segment merging (`merge_word_level_segments()`) |
| `search/repository.py` | `hybrid_search()`, `index_episode_embeddings()` |
| `api/search.py` | `/api/search/semantic` endpoint |
| `mcp/tools.py` | `semantic_search` MCP tool |
| `worker/manager.py` | Embedding worker (processes `EMBED` jobs) |
