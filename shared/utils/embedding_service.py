"""
Embedding helpers for semantic search over memory blocks.

Model is configurable via the EMBEDDING_MODEL env var (any litellm-supported
embedding model). Vectors are stored as JSON text on memory_blocks and ranked
with pure-Python cosine similarity — portable across sqlite/postgresql/mysql.
"""

import hashlib
import logging
import math
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "gemini/gemini-embedding-001"

# Keep the embedded text bounded; embedding APIs have input limits and
# block values can hold whole crawled pages.
MAX_EMBED_CHARS = 8000


def get_embedding_model() -> str:
    """Embedding model name, configurable via EMBEDDING_MODEL env var."""
    return os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)


def embedding_text_for_block(label: str, description: Optional[str], value: str) -> str:
    """Canonical text embedded for a memory block."""
    return f"{label}\n{description or ''}\n{(value or '')[:MAX_EMBED_CHARS]}"


def embedding_hash_for_text(text: str) -> str:
    """Stable hash used to detect stale embeddings after value changes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embed_texts(texts: List[str]) -> Optional[List[List[float]]]:
    """Embed texts via litellm. Returns None on any failure (caller falls back)."""
    if not texts:
        return []
    try:
        import litellm

        # litellm's Gemini provider reads GEMINI_API_KEY; MATE configures GOOGLE_API_KEY.
        if not os.getenv("GEMINI_API_KEY") and os.getenv("GOOGLE_API_KEY"):
            os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_API_KEY"]

        response = litellm.embedding(model=get_embedding_model(), input=texts)
        vectors: List[Optional[List[float]]] = [None] * len(texts)
        for item in response.data:
            index = item["index"] if isinstance(item, dict) else item.index
            vector = item["embedding"] if isinstance(item, dict) else item.embedding
            vectors[index] = list(vector)
        if any(v is None for v in vectors):
            logger.warning("Embedding response missing vectors for some inputs")
            return None
        return vectors  # type: ignore[return-value]
    except Exception as e:
        logger.warning(f"Embedding failed (model={get_embedding_model()}): {e}")
        return None


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity in pure Python; 0.0 for mismatched or zero vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
