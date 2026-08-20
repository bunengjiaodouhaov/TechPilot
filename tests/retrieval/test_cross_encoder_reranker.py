from __future__ import annotations

from typing import Any

import pytest

from app.retrieval.reranker import CrossEncoderRerankerProvider


class FakeCrossEncoder:
    def __init__(
        self,
        *,
        scores: list[float] | None = None,
    ) -> None:
        self.scores = scores
        self.calls: list[dict[str, Any]] = []

    def predict(
        self,
        pairs: list[tuple[str, str]],
        **kwargs: Any,
    ) -> list[float]:
        self.calls.append(
            {
                "pairs": pairs,
                **kwargs,
            }
        )

        if self.scores is not None:
            return self.scores

        return [
            float(index)
            for index, _ in enumerate(pairs, start=1)
        ]


def build_provider(
    *,
    model: FakeCrossEncoder | None = None,
) -> CrossEncoderRerankerProvider:
    return CrossEncoderRerankerProvider(
        model_name="test-reranker",
        batch_size=4,
        max_length=512,
        model=model or FakeCrossEncoder(),
    )


def test_score_returns_empty_list_without_model_call() -> None:
    model = FakeCrossEncoder()
    provider = build_provider(model=model)

    assert provider.score(
        query="query",
        documents=[],
    ) == []
    assert model.calls == []


def test_score_rejects_empty_query() -> None:
    provider = build_provider()

    with pytest.raises(
        ValueError,
        match="query must not be empty",
    ):
        provider.score(
            query="   ",
            documents=["document"],
        )


def test_score_rejects_empty_document() -> None:
    provider = build_provider()

    with pytest.raises(
        ValueError,
        match=r"documents\[1\] must not be empty",
    ):
        provider.score(
            query="query",
            documents=["valid", "   "],
        )


def test_score_builds_query_document_pairs_and_preserves_order() -> None:
    model = FakeCrossEncoder()
    provider = build_provider(model=model)

    scores = provider.score(
        query="  TechPilot reranker  ",
        documents=[
            " first chunk ",
            "second chunk",
        ],
    )

    assert scores == [1.0, 2.0]
    assert model.calls[0]["pairs"] == [
        ("TechPilot reranker", "first chunk"),
        ("TechPilot reranker", "second chunk"),
    ]


def test_score_uses_batched_predict_without_progress_bar() -> None:
    model = FakeCrossEncoder()
    provider = build_provider(model=model)

    provider.score(
        query="query",
        documents=["a", "b"],
    )

    call = model.calls[0]
    assert call["batch_size"] == 4
    assert call["show_progress_bar"] is False
    assert call["convert_to_numpy"] is True


def test_score_rejects_wrong_output_count() -> None:
    model = FakeCrossEncoder(scores=[0.5])
    provider = build_provider(model=model)

    with pytest.raises(
        ValueError,
        match="output count does not match input count",
    ):
        provider.score(
            query="query",
            documents=["a", "b"],
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("model_name", "", "model_name must not be empty"),
        ("batch_size", 0, "batch_size must be greater than zero"),
        ("max_length", 0, "max_length must be greater than zero"),
        ("device", "   ", "device must not be empty"),
    ],
)
def test_provider_rejects_invalid_configuration(
    field: str,
    value: object,
    message: str,
) -> None:
    kwargs: dict[str, object] = {
        "model_name": "test-reranker",
        "batch_size": 4,
        "max_length": 512,
        "device": None,
        "model": FakeCrossEncoder(),
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=message):
        CrossEncoderRerankerProvider(**kwargs)  # type: ignore[arg-type]
