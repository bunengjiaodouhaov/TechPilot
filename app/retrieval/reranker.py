from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RerankerProvider(Protocol):
    """Stable scoring interface used by the reranking service."""

    def score(
        self,
        *,
        query: str,
        documents: Sequence[str],
    ) -> list[float]:
        """Return one relevance score per document, preserving input order."""
        ...


class CrossEncoderRerankerProvider:
    """Sentence Transformers CrossEncoder adapter."""

    def __init__(
        self,
        *,
        model_name: str,
        batch_size: int = 8,
        max_length: int = 512,
        device: str | None = None,
        model: Any | None = None,
    ) -> None:
        if not model_name.strip():
            raise ValueError("model_name must not be empty")
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")
        if max_length <= 0:
            raise ValueError("max_length must be greater than zero")
        if device is not None and not device.strip():
            raise ValueError("device must not be empty")

        self._model_name = model_name
        self._batch_size = batch_size
        self._max_length = max_length
        self._device = device
        self._model = model

    def score(
        self,
        *,
        query: str,
        documents: Sequence[str],
    ) -> list[float]:
        normalized_query = self._validate_text(
            query,
            field_name="query",
        )

        if not documents:
            return []

        normalized_documents = [
            self._validate_text(
                document,
                field_name=f"documents[{index}]",
            )
            for index, document in enumerate(documents)
        ]

        pairs = [
            (normalized_query, document)
            for document in normalized_documents
        ]

        raw_scores = self._get_model().predict(
            pairs,
            batch_size=self._batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
        )

        if hasattr(raw_scores, "tolist"):
            raw_scores = raw_scores.tolist()

        scores = [float(score) for score in raw_scores]

        if len(scores) != len(normalized_documents):
            raise ValueError(
                "reranker output count does not match input count: "
                f"expected {len(normalized_documents)}, got {len(scores)}"
            )

        return scores

    def _get_model(self) -> Any:
        if self._model is None:
            # Import lazily so importing this module does not load PyTorch
            # or download a model.
            from sentence_transformers import CrossEncoder

            kwargs: dict[str, Any] = {
                "max_length": self._max_length,
            }
            if self._device is not None:
                kwargs["device"] = self._device

            self._model = CrossEncoder(
                self._model_name,
                **kwargs,
            )

        return self._model

    @staticmethod
    def _validate_text(
        text: str,
        *,
        field_name: str,
    ) -> str:
        if not isinstance(text, str):
            raise TypeError(f"{field_name} must be a string")

        stripped = text.strip()
        if not stripped:
            raise ValueError(f"{field_name} must not be empty")

        return stripped
