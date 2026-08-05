from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.answering.dto import StoredChunk
from app.retrieval.hybrid_dto import HybridSearchHit
from app.retrieval.reranking_service import (
    RerankingDataConsistencyError,
    RerankingService,
)


def build_hybrid_hit(
    *,
    point_id: int,
    chunk_id: str,
    rrf_score: float,
    chunk_index: int = 0,
    document_id: int = 10,
    document_name: str = "doc.md",
) -> HybridSearchHit:
    return HybridSearchHit(
        point_id=point_id,
        rrf_score=rrf_score,
        workspace_id=1,
        document_id=document_id,
        chunk_id=chunk_id,
        chunk_index=chunk_index,
        section="section",
        document_name=document_name,
        source_type="markdown",
        page_start=None,
        page_end=None,
        dense_rank=point_id,
        dense_score=0.9,
        bm25_rank=point_id,
        bm25_score=5.0,
    )


def build_stored_chunk(
    hit: HybridSearchHit,
    *,
    text: str | None = None,
    chunk_id: str | None = None,
) -> StoredChunk:
    return StoredChunk(
        chunk_db_id=hit.point_id,
        chunk_id=chunk_id or hit.chunk_id,
        document_id=hit.document_id,
        document_name=hit.document_name,
        source_type=hit.source_type,
        chunk_index=hit.chunk_index,
        section=hit.section,
        page_start=hit.page_start,
        page_end=hit.page_end,
        text=text or f"text for {hit.chunk_id}",
    )


class FakeHybridRetrievalService:
    def __init__(self, hits: list[HybridSearchHit]) -> None:
        self.hits = hits
        self.calls: list[dict[str, object]] = []

    async def search(
        self,
        *,
        query: str,
        workspace_id: int,
        candidate_limit: int = 20,
        limit: int = 5,
    ) -> list[HybridSearchHit]:
        self.calls.append(
            {
                "query": query,
                "workspace_id": workspace_id,
                "candidate_limit": candidate_limit,
                "limit": limit,
            }
        )
        return self.hits[:limit]


class FakeChunkRepository:
    def __init__(self, stored_chunks: dict[int, StoredChunk]) -> None:
        self.stored_chunks = stored_chunks
        self.calls: list[dict[str, object]] = []

    async def get_by_ids(
        self,
        *,
        chunk_ids: Sequence[int],
        workspace_id: int,
    ) -> dict[int, StoredChunk]:
        self.calls.append(
            {
                "chunk_ids": list(chunk_ids),
                "workspace_id": workspace_id,
            }
        )
        return {
            point_id: self.stored_chunks[point_id]
            for point_id in chunk_ids
            if point_id in self.stored_chunks
        }


class FakeRerankerProvider:
    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.calls: list[dict[str, object]] = []

    def score(
        self,
        *,
        query: str,
        documents: Sequence[str],
    ) -> list[float]:
        self.calls.append(
            {
                "query": query,
                "documents": list(documents),
            }
        )
        return self.scores


def build_service(
    *,
    hits: list[HybridSearchHit],
    stored_chunks: dict[int, StoredChunk],
    scores: list[float],
) -> tuple[
    RerankingService,
    FakeHybridRetrievalService,
    FakeChunkRepository,
    FakeRerankerProvider,
]:
    hybrid = FakeHybridRetrievalService(hits)
    repository = FakeChunkRepository(stored_chunks)
    provider = FakeRerankerProvider(scores)
    service = RerankingService(
        hybrid_retrieval_service=hybrid,
        chunk_repository=repository,
        reranker_provider=provider,
    )
    return service, hybrid, repository, provider


@pytest.mark.asyncio
async def test_search_returns_empty_without_loading_chunks_or_reranking() -> None:
    service, hybrid, repository, provider = build_service(
        hits=[],
        stored_chunks={},
        scores=[],
    )

    results = await service.search(
        query="query",
        workspace_id=1,
    )

    assert results == []
    assert len(hybrid.calls) == 1
    assert repository.calls == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_search_uses_candidate_limit_and_rerank_depth_as_distinct_boundaries() -> None:
    hit = build_hybrid_hit(
        point_id=1,
        chunk_id="chunk-1",
        rrf_score=0.03,
    )
    service, hybrid, _, _ = build_service(
        hits=[hit],
        stored_chunks={1: build_stored_chunk(hit)},
        scores=[0.8],
    )

    await service.search(
        query=" query ",
        workspace_id=1,
        candidate_limit=20,
        rerank_depth=10,
        limit=5,
    )

    assert hybrid.calls == [
        {
            "query": "query",
            "workspace_id": 1,
            "candidate_limit": 20,
            "limit": 10,
        }
    ]


@pytest.mark.asyncio
async def test_search_scores_authoritative_postgres_text_in_hybrid_order() -> None:
    first = build_hybrid_hit(
        point_id=1,
        chunk_id="chunk-1",
        rrf_score=0.04,
    )
    second = build_hybrid_hit(
        point_id=2,
        chunk_id="chunk-2",
        rrf_score=0.03,
    )
    service, _, repository, provider = build_service(
        hits=[first, second],
        stored_chunks={
            1: build_stored_chunk(first, text="postgres first"),
            2: build_stored_chunk(second, text="postgres second"),
        },
        scores=[0.2, 0.9],
    )

    results = await service.search(
        query="question",
        workspace_id=1,
        candidate_limit=20,
        rerank_depth=20,
        limit=2,
    )

    assert repository.calls[0]["chunk_ids"] == [1, 2]
    assert provider.calls[0]["documents"] == [
        "postgres first",
        "postgres second",
    ]
    assert [hit.hybrid_hit.chunk_id for hit in results] == [
        "chunk-2",
        "chunk-1",
    ]
    assert [hit.original_rank for hit in results] == [2, 1]
    assert [hit.rerank_rank for hit in results] == [1, 2]
    assert [hit.reranker_score for hit in results] == [0.9, 0.2]


@pytest.mark.asyncio
async def test_search_preserves_hybrid_diagnostics_after_reranking() -> None:
    hit = build_hybrid_hit(
        point_id=3,
        chunk_id="chunk-3",
        rrf_score=0.031,
    )
    service, _, _, _ = build_service(
        hits=[hit],
        stored_chunks={3: build_stored_chunk(hit)},
        scores=[8.75],
    )

    results = await service.search(
        query="question",
        workspace_id=1,
    )

    result = results[0]
    assert result.hybrid_hit is hit
    assert result.hybrid_hit.rrf_score == pytest.approx(0.031)
    assert result.hybrid_hit.dense_rank == 3
    assert result.hybrid_hit.bm25_rank == 3
    assert result.reranker_score == pytest.approx(8.75)


@pytest.mark.asyncio
async def test_search_uses_original_rank_as_stable_tie_breaker() -> None:
    first = build_hybrid_hit(
        point_id=1,
        chunk_id="chunk-1",
        rrf_score=0.04,
    )
    second = build_hybrid_hit(
        point_id=2,
        chunk_id="chunk-2",
        rrf_score=0.03,
    )
    service, _, _, _ = build_service(
        hits=[first, second],
        stored_chunks={
            1: build_stored_chunk(first),
            2: build_stored_chunk(second),
        },
        scores=[0.5, 0.5],
    )

    results = await service.search(
        query="question",
        workspace_id=1,
        limit=2,
    )

    assert [hit.hybrid_hit.chunk_id for hit in results] == [
        "chunk-1",
        "chunk-2",
    ]


@pytest.mark.asyncio
async def test_search_applies_final_limit_after_reranking() -> None:
    hits = [
        build_hybrid_hit(
            point_id=index,
            chunk_id=f"chunk-{index}",
            rrf_score=0.04 - index * 0.001,
        )
        for index in range(1, 4)
    ]
    service, _, _, _ = build_service(
        hits=hits,
        stored_chunks={
            hit.point_id: build_stored_chunk(hit)
            for hit in hits
        },
        scores=[0.1, 0.9, 0.8],
    )

    results = await service.search(
        query="question",
        workspace_id=1,
        candidate_limit=20,
        rerank_depth=20,
        limit=2,
    )

    assert [hit.hybrid_hit.chunk_id for hit in results] == [
        "chunk-2",
        "chunk-3",
    ]


@pytest.mark.asyncio
async def test_search_rejects_missing_postgres_candidate() -> None:
    hit = build_hybrid_hit(
        point_id=1,
        chunk_id="chunk-1",
        rrf_score=0.04,
    )
    service, _, _, provider = build_service(
        hits=[hit],
        stored_chunks={},
        scores=[0.8],
    )

    with pytest.raises(
        RerankingDataConsistencyError,
        match="missing from PostgreSQL",
    ):
        await service.search(
            query="question",
            workspace_id=1,
        )

    assert provider.calls == []


@pytest.mark.asyncio
async def test_search_rejects_hybrid_postgres_identity_mismatch() -> None:
    hit = build_hybrid_hit(
        point_id=1,
        chunk_id="chunk-1",
        rrf_score=0.04,
    )
    service, _, _, provider = build_service(
        hits=[hit],
        stored_chunks={
            1: build_stored_chunk(
                hit,
                chunk_id="wrong-chunk",
            )
        },
        scores=[0.8],
    )

    with pytest.raises(
        RerankingDataConsistencyError,
        match="identity mismatch",
    ):
        await service.search(
            query="question",
            workspace_id=1,
        )

    assert provider.calls == []


@pytest.mark.asyncio
async def test_search_rejects_wrong_provider_output_count() -> None:
    first = build_hybrid_hit(
        point_id=1,
        chunk_id="chunk-1",
        rrf_score=0.04,
    )
    second = build_hybrid_hit(
        point_id=2,
        chunk_id="chunk-2",
        rrf_score=0.03,
    )
    service, _, _, _ = build_service(
        hits=[first, second],
        stored_chunks={
            1: build_stored_chunk(first),
            2: build_stored_chunk(second),
        },
        scores=[0.9],
    )

    with pytest.raises(
        ValueError,
        match="output count does not match candidate count",
    ):
        await service.search(
            query="question",
            workspace_id=1,
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"candidate_limit": 0}, "candidate_limit must be greater than zero"),
        ({"rerank_depth": 0}, "rerank_depth must be greater than zero"),
        ({"limit": 0}, "limit must be greater than zero"),
        (
            {"candidate_limit": 5, "rerank_depth": 11},
            "rerank_depth must not exceed twice candidate_limit",
        ),
        (
            {"rerank_depth": 4, "limit": 5},
            "rerank_depth must be greater than or equal to limit",
        ),
    ],
)
@pytest.mark.asyncio
async def test_search_rejects_invalid_depth_configuration(
    kwargs: dict[str, int],
    message: str,
) -> None:
    service, _, _, _ = build_service(
        hits=[],
        stored_chunks={},
        scores=[],
    )

    with pytest.raises(ValueError, match=message):
        await service.search(
            query="question",
            workspace_id=1,
            **kwargs,
        )
