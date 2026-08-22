from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.retrieval.answer_retrieval_adapter import AnswerRetrievalAdapter


class FakeRerankingService:
    def __init__(self) -> None:
        self.calls = []

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        hybrid = SimpleNamespace(
            point_id=10,
            workspace_id=7,
            document_id=20,
            chunk_id="chunk-a",
            chunk_index=3,
            section=None,
            document_name="doc.pdf",
            source_type="pdf",
            page_start=4,
            page_end=4,
        )
        return [
            SimpleNamespace(
                hybrid_hit=hybrid,
                reranker_score=2.5,
                original_rank=2,
                rerank_rank=1,
            )
        ]


@pytest.mark.asyncio
async def test_adapter_returns_vector_search_hits() -> None:
    service = FakeRerankingService()
    adapter = AnswerRetrievalAdapter(
        reranking_service=service,
        candidate_limit=40,
        rerank_depth=20,
    )

    hits = await adapter.search(
        query="question",
        workspace_id=7,
        limit=5,
    )

    assert len(hits) == 1
    assert hits[0].point_id == 10
    assert hits[0].score == 2.5
    assert hits[0].payload.chunk_id == "chunk-a"
    assert service.calls[0]["candidate_limit"] == 40
    assert service.calls[0]["rerank_depth"] == 20
    assert service.calls[0]["limit"] == 5
