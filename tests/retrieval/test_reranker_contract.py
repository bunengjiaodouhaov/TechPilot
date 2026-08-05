from dataclasses import FrozenInstanceError

import pytest

from app.retrieval.hybrid_dto import HybridSearchHit
from app.retrieval.reranker import RerankerProvider
from app.retrieval.reranker_dto import RerankCandidate, RerankedSearchHit


class FakeReranker:
    def score(
        self,
        *,
        query: str,
        documents: list[str],
    ) -> list[float]:
        return [
            float(index)
            for index, _ in enumerate(documents, start=1)
        ]


def build_hybrid_hit() -> HybridSearchHit:
    return HybridSearchHit(
        point_id=101,
        rrf_score=0.031,
        workspace_id=1,
        document_id=10,
        chunk_id="chunk-101",
        chunk_index=3,
        section="Retrieval",
        document_name="techpilot.md",
        source_type="markdown",
        page_start=None,
        page_end=None,
        dense_rank=2,
        dense_score=0.91,
        bm25_rank=4,
        bm25_score=6.2,
    )


def test_rerank_candidate_keeps_hybrid_identity_and_text() -> None:
    hybrid_hit = build_hybrid_hit()

    candidate = RerankCandidate(
        hybrid_hit=hybrid_hit,
        text="authoritative PostgreSQL chunk text",
        original_rank=2,
    )

    assert candidate.hybrid_hit is hybrid_hit
    assert candidate.text == "authoritative PostgreSQL chunk text"
    assert candidate.original_rank == 2


def test_reranked_hit_keeps_original_hybrid_diagnostics() -> None:
    hybrid_hit = build_hybrid_hit()

    hit = RerankedSearchHit(
        hybrid_hit=hybrid_hit,
        reranker_score=8.75,
        original_rank=2,
        rerank_rank=1,
    )

    assert hit.hybrid_hit.rrf_score == pytest.approx(0.031)
    assert hit.hybrid_hit.dense_rank == 2
    assert hit.hybrid_hit.bm25_rank == 4
    assert hit.reranker_score == pytest.approx(8.75)
    assert hit.original_rank == 2
    assert hit.rerank_rank == 1


def test_rerank_dtos_are_immutable() -> None:
    candidate = RerankCandidate(
        hybrid_hit=build_hybrid_hit(),
        text="chunk text",
        original_rank=1,
    )

    with pytest.raises(FrozenInstanceError):
        candidate.original_rank = 2  # type: ignore[misc]


def test_reranker_provider_contract_accepts_compatible_implementation() -> None:
    provider = FakeReranker()

    assert isinstance(provider, RerankerProvider)
    assert provider.score(
        query="query",
        documents=["a", "b"],
    ) == [1.0, 2.0]
