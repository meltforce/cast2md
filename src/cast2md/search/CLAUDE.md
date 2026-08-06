# Unified Search

The main search (`/search`) provides unified search across both episode metadata and transcript content. Users can find episodes by title/description or by what was said in the transcript.

## Result Types

Search returns two types of results:

1. **Episode Matches** (`result_type: "episode"`)
   - Matches episode title or description
   - Shows "title" badge
   - Links directly to episode (no timestamp)

2. **Transcript Matches** (`result_type: "transcript"`)
   - Matches content within transcripts
   - Shows "keyword", "semantic", or "both" badge
   - Links to episode with timestamp

## How It Works

1. **Hybrid Search**: Combines PostgreSQL full-text search with vector similarity using Reciprocal Rank Fusion (RRF)
2. **Embeddings**: Uses `sentence-transformers` with multilingual model (384-dim, ~470MB)
3. **Vector Storage**: pgvector extension with HNSW index for fast approximate nearest neighbor search
4. **Episode Search**: Searches `episode_search` table for title/description matches

## Embedding Model

Uses `paraphrase-multilingual-MiniLM-L12-v2` for German language support:
- 50+ languages including German
- Understands semantic similarity (e.g., "kaltbaden" ≈ "eisbaden")
- 384 dimensions, ~470MB model size
- Configured in `search/embeddings.py`

## Segment Merging

Transcripts (both Whisper and external) can have word-level timestamps where each word is a separate segment. The system automatically merges these into phrases:

- Merging happens during indexing (FTS and embeddings) and display
- Phrase boundaries: punctuation, pauses (>1.5s), or max 200 chars
- Improves both search quality (fewer noisy results) and readability
- See `search/parser.py:merge_word_level_segments()`

Embedding generation runs as a background worker processing `EMBED` jobs in `worker/manager.py`.

## Reindexing

```bash
# Reindex FTS only
cast2md reindex-transcripts

# Reindex FTS and regenerate embeddings (needed after model change)
cast2md reindex-transcripts --embeddings
```

## Startup Behavior

- **Embeddings**: Persisted in PostgreSQL (survives restarts)
- **Model loading**: ~3 seconds on first semantic query after restart
- **Background worker**: Automatically generates embeddings for new transcripts

## pgvector Notes

- Uses HNSW index for fast approximate nearest neighbor search
- Vector column defined as `vector(384)` matching embedding dimension
- Cosine distance used for similarity: `embedding <=> query_embedding`
