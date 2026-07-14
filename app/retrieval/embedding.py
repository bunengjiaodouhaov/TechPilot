from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable


Vector = list[float]


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Stable interface used by indexing and retrieval services."""

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        """Embed document chunks while preserving input order."""

    def embed_query(self, query: str) -> Vector:
        """Embed one user query."""


class SentenceTransformerEmbeddingProvider:
    """Sentence Transformers implementation for E5-style embeddings."""

    def __init__(
        self,
        *,
        model_name: str,
        dimension: int,
        batch_size: int,
        model: Any | None = None,
    ) -> None:
        if not model_name.strip():
            raise ValueError("model_name must not be empty")
        if dimension <= 0:
            raise ValueError("dimension must be greater than zero")
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")

        self._model_name = model_name
        self._dimension = dimension
        self._batch_size = batch_size
        self._model = model

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        if not texts:
            return []

        prepared = [
            f"passage: {self._validate_text(text, field_name='document text')}"
            for text in texts
        ]
        return self._encode(prepared)

    def embed_query(self, query: str) -> Vector:
        prepared = f"query: {self._validate_text(query, field_name='query')}"
        vectors = self._encode([prepared])
        return vectors[0]

    def _get_model(self) -> Any:
        if self._model is None:
            # Import lazily so importing this module does not load PyTorch
            # or download a model.
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)

        return self._model

    def _encode(self, texts: Sequence[str]) -> list[Vector]:
        encoded = self._get_model().encode(
            list(texts),
            batch_size=self._batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )

        raw_vectors = encoded.tolist() if hasattr(encoded, "tolist") else encoded
        vectors = [
            [float(value) for value in vector]
            for vector in raw_vectors
        ]

        self._validate_vectors(
            vectors=vectors,
            expected_count=len(texts),
        )
        return vectors

    def _validate_vectors(
        self,
        *,
        vectors: Sequence[Sequence[float]],
        expected_count: int,
    ) -> None:
        if len(vectors) != expected_count:
            raise ValueError(
                "embedding output count does not match input count: "
                f"expected {expected_count}, got {len(vectors)}"
            )

        for index, vector in enumerate(vectors):
            if len(vector) != self._dimension:
                raise ValueError(
                    f"embedding dimension mismatch at index {index}: "
                    f"expected {self._dimension}, got {len(vector)}"
                )

    @staticmethod
    def _validate_text(text: str, *, field_name: str) -> str:
        if not isinstance(text, str):
            raise TypeError(f"{field_name} must be a string")

        stripped = text.strip()
        if not stripped:
            raise ValueError(f"{field_name} must not be empty")

        return stripped
