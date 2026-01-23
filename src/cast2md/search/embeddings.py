"""Embedding generation for semantic search using sentence-transformers."""

import hashlib
import logging
import struct
from typing import Optional

logger = logging.getLogger(__name__)

# Default embedding model - multilingual for German support
# paraphrase-multilingual-MiniLM-L12-v2: 50+ languages, 384 dims, ~470MB
DEFAULT_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM = 384  # Dimension of embeddings (same for multilingual model)

# Singleton model instance (lazy-loaded)
_model: Optional["SentenceTransformer"] = None  # type: ignore
_model_name: Optional[str] = None


def _get_model(model_name: str = DEFAULT_MODEL_NAME):
    """Get or create the embedding model (lazy-loaded singleton).

    Args:
        model_name: Name of the sentence-transformers model to use.

    Returns:
        Loaded SentenceTransformer model.

    Raises:
        ImportError: If sentence-transformers is not installed.
    """
    global _model, _model_name

    if _model is not None and _model_name == model_name:
        return _model

    try:
        from sentence_transformers import SentenceTransformer

        logger.info(f"Loading embedding model: {model_name}")
        _model = SentenceTransformer(model_name)
        _model_name = model_name
        logger.info(f"Embedding model loaded: {model_name}")
        return _model
    except ImportError:
        raise ImportError(
            "sentence-transformers is required for semantic search. "
            "Install with: pip install sentence-transformers"
        )


def text_hash(text: str) -> str:
    """Generate a hash of text for change detection.

    Args:
        text: Text to hash.

    Returns:
        SHA-256 hash of the text (first 16 chars).
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def generate_embedding(text: str, model_name: str = DEFAULT_MODEL_NAME) -> bytes:
    """Generate embedding for a single text.

    Args:
        text: Text to embed.
        model_name: Name of the model to use.

    Returns:
        Embedding as bytes (float32 array packed as binary).
    """
    model = _get_model(model_name)
    embedding = model.encode(text, convert_to_numpy=True)
    # Pack as float32 binary for pgvector
    return struct.pack(f"{len(embedding)}f", *embedding)


def generate_embeddings_batch(
    texts: list[str], model_name: str = DEFAULT_MODEL_NAME, as_numpy: bool = False
) -> list:
    """Generate embeddings for multiple texts efficiently.

    Args:
        texts: List of texts to embed.
        model_name: Name of the model to use.
        as_numpy: If True, return numpy arrays. If False, return packed bytes.

    Returns:
        List of embeddings as bytes (default) or numpy arrays.
    """
    if not texts:
        return []

    model = _get_model(model_name)
    embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)

    if as_numpy:
        # Return numpy arrays for pgvector
        return [emb for emb in embeddings]
    else:
        # Pack each embedding as binary for pgvector
        return [struct.pack(f"{len(emb)}f", *emb) for emb in embeddings]


def embedding_to_floats(embedding_bytes: bytes) -> list[float]:
    """Convert embedding bytes back to float list.

    Args:
        embedding_bytes: Binary embedding data.

    Returns:
        List of float values.
    """
    count = len(embedding_bytes) // 4  # 4 bytes per float32
    return list(struct.unpack(f"{count}f", embedding_bytes))


def is_embeddings_available() -> bool:
    """Check if embedding generation is available.

    Returns:
        True if sentence-transformers can be imported.
    """
    try:
        import sentence_transformers  # noqa: F401

        return True
    except ImportError:
        return False


def get_model_name() -> str:
    """Get the currently configured model name.

    Returns:
        Model name string.
    """
    return _model_name or DEFAULT_MODEL_NAME
