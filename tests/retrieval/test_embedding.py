from __future__ import annotations

import math
from typing import Any

import pytest

from app.retrieval.embedding import SentenceTransformerEmbeddingProvider


class FakeEmbeddingModel:
    def __init__(self, *, dimension: int = 3) -> None:
        self.dimension = dimension
        self.calls: list[dict[str, Any]] = []

    def encode(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        self.calls.append({"texts": texts, **kwargs})

        base = [1.0 / math.sqrt(self.dimension)] * self.dimension
        return [base.copy() for _ in texts]


def build_provider(
    *,
    model: FakeEmbeddingModel | None = None,
    dimension: int = 3,
) -> SentenceTransformerEmbeddingProvider:
    return SentenceTransformerEmbeddingProvider(
        model_name="test-model",
        dimension=dimension,
        batch_size=2,
        model=model or FakeEmbeddingModel(dimension=dimension),
    )


def test_embed_documents_returns_empty_list_for_empty_input() -> None:
    model = FakeEmbeddingModel()
    provider = build_provider(model=model)

    assert provider.embed_documents([]) == []
    assert model.calls == []


def test_embed_query_rejects_empty_text() -> None:
    provider = build_provider()

    with pytest.raises(ValueError, match="query must not be empty"):
        provider.embed_query("   ")


def test_embed_documents_adds_passage_prefix_and_preserves_count() -> None:
    model = FakeEmbeddingModel()
    provider = build_provider(model=model)

    vectors = provider.embed_documents(["first", " second "])

    assert len(vectors) == 2
    assert model.calls[0]["texts"] == [
        "passage: first",
        "passage: second",
    ]


def test_embed_query_adds_query_prefix() -> None:
    model = FakeEmbeddingModel()
    provider = build_provider(model=model)

    vector = provider.embed_query(" FastAPI IOC ")

    assert len(vector) == 3
    assert model.calls[0]["texts"] == ["query: FastAPI IOC"]


def test_provider_requests_batching_and_normalization() -> None:
    model = FakeEmbeddingModel()
    provider = build_provider(model=model)

    vectors = provider.embed_documents(["a", "b"])

    call = model.calls[0]
    assert call["batch_size"] == 2
    assert call["normalize_embeddings"] is True
    assert call["convert_to_numpy"] is True
    assert call["show_progress_bar"] is False

    for vector in vectors:
        norm = math.sqrt(sum(value * value for value in vector))
        assert norm == pytest.approx(1.0)


def test_provider_rejects_wrong_vector_dimension() -> None:
    model = FakeEmbeddingModel(dimension=2)
    provider = build_provider(model=model, dimension=3)

    with pytest.raises(ValueError, match="dimension mismatch"):
        provider.embed_query("query")
