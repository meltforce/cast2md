# Python Vector Solutions

To implement Semantic Search in `cast2md`, you need two components:
1.  **Generation (Inference)**: Turning text into numbers.
2.  **Storage & Search**: Saving those numbers and finding similar ones.

## 1. Generation: `sentence-transformers`
This is the industry standard Python library for local embedding generation.
- **Library**: [`sentence-transformers`](https://sbert.net)
- **Model**: `all-MiniLM-L6-v2` (Fast, small, good quality).
- **Pros**: Runs locally (CPU/GPU), no API costs, privacy-preserving.

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("all-MiniLM-L6-v2")
vector = model.encode("We raised a lot of capital")
# Output: [0.12, 0.88, ...] (384 dimensions)
```

## 2. Storage & Search: `sqlite-vec`
Since `cast2md` already uses SQLite, **`sqlite-vec`** is the perfect fit. It is a new, high-performance vector search extension for SQLite.
- **Library**: `sqlite-vec` (Python bindings available).
- **Architecture**: Keeps everything in your existing `cast2md.db`. No new servers (like Postgres/Chroma) needed.
- **Performance**: Very fast for the scale of a personal podcast library (up to millions of vectors).

## Recommended Architecture for `cast2md`
1.  **Add Column**: Add a `embedding` column (blob) to `transcript_segments`.
2.  **Background Worker**:
    -   When a transcript is generated/downloaded:
    -   Chunk it into sentences/paragraphs.
    -   Use `sentence-transformers` to generate vectors.
    -   Save vectors to SQLite using `sqlite-vec`.
3.  **Search**:
    -   User query -> Vector.
    -   SQL query: `SELECT * FROM segments WHERE vec_distance_cosine(embedding, ?) < 0.3`.

## Alternative: ChromaDB
If you don't want to mess with SQLite extensions, **ChromaDB** is a popular strictly-Python vector store.
- **Pros**: Very easy API.
- **Cons**: Adds a separate database/service to manage. `sqlite-vec` is cleaner for this specific project.

## Scalability Analysis: Is SQLite Enough?
**Short Answer: Yes.**

**The Math (1,000 Episodes):**
- 1 Episode ≈ 1 hour ≈ 9,000 words.
- Chunk size ≈ 100 words/vector.
- Vectors per episode ≈ 90.
- **Total Vectors**: 1,000 episodes * 90 = **90,000 vectors**.

**Performance Limits:**
- **Keyword Search (FTS5)**: SQLite handles **millions** of rows trivially. 10,000 episodes would be instant.
- **Vector Search (`sqlite-vec`)**:
    - Brute-force search is fast enough for up to ~100k-500k vectors on modern CPUs.
    - Creating an index allows scaling to **millions** of vectors.
    - Since this is a single-user app (low traffic), you won't hit concurrency limits.

**Recommendation**: Stick with SQLite. It avoids the "Enterprise Overhead" of Postgres/Weaviate while easily serving a massive personal library.
