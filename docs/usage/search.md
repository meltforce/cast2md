# Search

![Search](../assets/images/search.png)

The search page (`/search`) provides unified search across episode metadata and transcript content.

## Search Modes

| Mode | Description |
|------|-------------|
| **Hybrid** (default) | Combines keyword and semantic search |
| **Keyword** | PostgreSQL full-text search only |
| **Semantic** | Vector similarity search only |

## Result Types

Search returns two types of results:

| Badge | Description |
|-------|-------------|
| "title" | Matched episode title or description |
| "keyword" | Matched transcript text via full-text search |
| "semantic" | Matched transcript meaning via embeddings |
| "both" | Matched by both keyword and semantic search |

Transcript results include timestamps and link directly to the relevant position in the episode.

## Tips

- **Hybrid mode** works best for most queries -- it combines exact keyword matching with meaning-based search
- **Semantic mode** is useful when you don't remember the exact words -- search for concepts and synonyms
- **Keyword mode** is best when you know the exact phrase that was said

See [Search Architecture](../features/search.md) for technical details on how search works.
