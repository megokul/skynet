"""
Vector Index — Embedding generation for semantic memory search.

Supports multiple embedding providers:
- Gemini Embedding API (Google AI)
- sentence-transformers (local, no API cost)
- Future: OpenAI, Anthropic, etc.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("skynet.memory.vector_index")


class VectorIndexer:
    """
    Base interface for vector embedding generation.
    """

    async def generate_embedding(self, text: str) -> list[float]:
        """Generate embedding vector for text."""
        raise NotImplementedError

    def get_dimension(self) -> int:
        """Get embedding dimension."""
        raise NotImplementedError


class GeminiEmbedding(VectorIndexer):
    """
    Gemini Embedding API (Google AI).

    Free tier available, good quality embeddings.
    Dimension: 768
    """

    def __init__(self, api_key: str | None = None):
        """
        Initialize Gemini embedder.

        Args:
            api_key: Google AI API key (or from GOOGLE_AI_API_KEY env var)
        """
        self.api_key = api_key or os.getenv("GOOGLE_AI_API_KEY")
        if not self.api_key:
            raise ValueError("GOOGLE_AI_API_KEY not set")

        try:
            import google.generativeai as genai

            genai.configure(api_key=self.api_key)
            self.genai = genai
            self._initialized = True
        except ImportError:
            logger.error("google-genai not installed. Install with: pip install google-genai")
            self._initialized = False

    async def generate_embedding(self, text: str) -> list[float]:
        """
        Generate embedding using Gemini API.

        Args:
            text: Text to embed

        Returns:
            768-dimensional embedding vector
        """
        if not self._initialized:
            raise RuntimeError("Gemini not initialized")

        try:
            # Use Gemini embedding model
            result = self.genai.embed_content(
                model="models/embedding-001",
                content=text,
                task_type="retrieval_document",  # For semantic search
            )

            embedding = result["embedding"]
            return embedding

        except Exception as e:
            logger.error(f"Gemini embedding failed: {e}")
            # Return zero vector as fallback
            return [0.0] * self.get_dimension()

    def get_dimension(self) -> int:
        """Get embedding dimension."""
        return 768


class SentenceTransformerEmbedding(VectorIndexer):
    """
    Sentence-Transformers (local, no API cost).

    Good for development/testing without API costs.
    Dimension: varies by model (default: 384)
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """
        Initialize sentence-transformers embedder.

        Args:
            model_name: HuggingFace model name
        """
        self.model_name = model_name

        try:
            from sentence_transformers import SentenceTransformer

            self.model = SentenceTransformer(model_name)
            self._dimension = self.model.get_sentence_embedding_dimension()
            self._initialized = True
            logger.info(f"Loaded sentence-transformers model: {model_name} (dim={self._dimension})")
        except ImportError:
            logger.error(
                "sentence-transformers not installed. "
                "Install with: pip install sentence-transformers"
            )
            self._initialized = False

    async def generate_embedding(self, text: str) -> list[float]:
        """
        Generate embedding using sentence-transformers.

        Args:
            text: Text to embed

        Returns:
            Embedding vector (dimension varies by model)
        """
        if not self._initialized:
            raise RuntimeError("SentenceTransformer not initialized")

        try:
            # Generate embedding
            embedding = self.model.encode(text, convert_to_numpy=True)
            return embedding.tolist()

        except Exception as e:
            logger.error(f"Sentence-transformer embedding failed: {e}")
            return [0.0] * self._dimension

    def get_dimension(self) -> int:
        """Get embedding dimension."""
        return self._dimension if self._initialized else 384


class MockEmbedding(VectorIndexer):
    """
    Mock embedder for testing (no actual embeddings).

    Returns random vectors for development/testing.
    """

    def __init__(self, dimension: int = 768):
        """Initialize mock embedder."""
        self.dimension = dimension

    async def generate_embedding(self, text: str) -> list[float]:
        """Generate mock embedding (hash-based)."""
        import hashlib

        # Generate deterministic "embedding" from text hash
        hash_val = int(hashlib.md5(text.encode()).hexdigest(), 16)

        # Pseudo-random vector based on hash
        embedding = []
        for i in range(self.dimension):
            val = ((hash_val + i) % 10000) / 10000.0
            embedding.append(val)

        return embedding

    def get_dimension(self) -> int:
        """Get embedding dimension."""
        return self.dimension


# ============================================================================
# Factory Function
# ============================================================================


def create_vector_indexer(
    provider: str = "gemini", **kwargs: Any
) -> VectorIndexer:
    """
    Create vector indexer based on provider.

    Args:
        provider: "gemini", "sentence-transformers", or "mock"
        **kwargs: Provider-specific arguments

    Returns:
        VectorIndexer instance
    """
    if provider == "gemini":
        api_key = kwargs.get("api_key")
        return GeminiEmbedding(api_key=api_key)

    elif provider == "sentence-transformers":
        model_name = kwargs.get("model_name", "all-MiniLM-L6-v2")
        return SentenceTransformerEmbedding(model_name=model_name)

    elif provider == "mock":
        dimension = kwargs.get("dimension", 768)
        return MockEmbedding(dimension=dimension)

    else:
        raise ValueError(f"Unknown embedding provider: {provider}")
