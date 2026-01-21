# Concept: Vector Embeddings (Semantic Search)

## The Problem with Keywords
Current Implementation (FTS5):
- **Query**: "Money"
- **Matches**: "Money", "money"
- **Misses**: "Cash", "Currency", "Dollars", "Finance", "Capital"

If a podcaster says *"We raised a lot of capital"*, a search for *"money"* will **FAIL**.

## The Solution: Vectors
**Vector Embeddings** convert text into lists of numbers (coordinates) based on *meaning*.

### How it works (Analogy)
Imagine a 3D map of concepts:
- **"Dog"** and **"Puppy"** are very close together.
- **"Cat"** is somewhat close to "Dog" (both pets).
- **"Car"** is far away from both.

### In Practice
When you use embeddings:
1.  **"Money"** becomes `[0.2, 0.9, 0.1]`
2.  **"Capital"** becomes `[0.21, 0.88, 0.12]` (Very close mathematically!)
3.  **"Banana"** becomes `[0.9, 0.1, 0.0]` (Far away)

## Why it matters for Podcasts
Podcasts are conversational. People use synonyms, slang, and analogies.
- **User asks**: "Episodes about **mental health**"
- **Keyword Search**: Finds "mental health".
- **Vector Search**: Finds "mental health", "burnout", "depression", "anxiety", "wellness", "therapy".

**Vectors allow you to search for *concepts*, not just words.**
