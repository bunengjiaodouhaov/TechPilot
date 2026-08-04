from typing import Any

import pytest

from app.retrieval.bm25_dto import BM25SearchHit
from app.retrieval.dto import (
    ChunkVectorPayload,
    VectorSearchHit,
)
from app.retrieval.hybrid_retrieval_service import (
    HybridRetrievalService,
)


class FakeDenseRetrievalService:
    def __init__(
        self,
        hits: list[VectorSearchHit],
    ) -> None:
        self.hits = hits
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        *,
        query: str,
        workspace_id: int,
        limit: int = 5,
    ) -> list[VectorSearchHit]:
        self.calls.append(
            {
                "query": query,
                "workspace_id": workspace_id,
                "limit": limit,
            }
        )

        return self.hits[:limit]


class FakeBM25RetrievalService:
    def __init__(
        self,
        hits: list[BM25SearchHit],
    ) -> None:
        self.hits = hits
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        *,
        query: str,
        workspace_id: int,
        limit: int = 5,
    ) -> list[BM25SearchHit]:
        self.calls.append(
            {
                "query": query,
                "workspace_id": workspace_id,
                "limit": limit,
            }
        )

        return self.hits[:limit]


def make_dense_hit(
    *,
    point_id: int,
    chunk_id: str,
    score: float,
    workspace_id: int = 1,
    document_id: int | None = None,
    chunk_index: int = 0,
) -> VectorSearchHit:
    resolved_document_id = (
        document_id
        if document_id is not None
        else point_id
    )

    return VectorSearchHit(
        point_id=point_id,
        score=score,
        payload=ChunkVectorPayload(
            workspace_id=workspace_id,
            document_id=resolved_document_id,
            chunk_id=chunk_id,
            chunk_index=chunk_index,
            section=f"section-{chunk_id}",
            document_name=f"document-{resolved_document_id}.md",
            source_type="markdown",
            page_start=None,
            page_end=None,
        ),
    )


def make_bm25_hit(
    *,
    point_id: int,
    chunk_id: str,
    score: float,
    workspace_id: int = 1,
    document_id: int | None = None,
    chunk_index: int = 0,
) -> BM25SearchHit:
    resolved_document_id = (
        document_id
        if document_id is not None
        else point_id
    )

    return BM25SearchHit(
        point_id=point_id,
        score=score,
        workspace_id=workspace_id,
        document_id=resolved_document_id,
        chunk_id=chunk_id,
        chunk_index=chunk_index,
        section=f"section-{chunk_id}",
        document_name=f"document-{resolved_document_id}.md",
        source_type="markdown",
        page_start=None,
        page_end=None,
    )


@pytest.mark.asyncio
async def test_search_uses_candidate_depth_before_final_limit() -> None:
    dense = FakeDenseRetrievalService(
        [
            make_dense_hit(
                point_id=1,
                chunk_id="chunk-a",
                score=0.90,
            ),
            make_dense_hit(
                point_id=2,
                chunk_id="chunk-b",
                score=0.80,
            ),
            make_dense_hit(
                point_id=4,
                chunk_id="chunk-d",
                score=0.70,
            ),
        ]
    )

    bm25 = FakeBM25RetrievalService(
        [
            make_bm25_hit(
                point_id=2,
                chunk_id="chunk-b",
                score=20.0,
            ),
            make_bm25_hit(
                point_id=3,
                chunk_id="chunk-c",
                score=15.0,
            ),
            make_bm25_hit(
                point_id=1,
                chunk_id="chunk-a",
                score=10.0,
            ),
        ]
    )

    service = HybridRetrievalService(
        dense_retrieval_service=dense,
        bm25_retrieval_service=bm25,
        rrf_k=60,
    )

    hits = await service.search(
        query="  FastAPI retrieval  ",
        workspace_id=1,
        candidate_limit=3,
        limit=2,
    )

    assert dense.calls == [
        {
            "query": "FastAPI retrieval",
            "workspace_id": 1,
            "limit": 3,
        }
    ]

    assert bm25.calls == [
        {
            "query": "FastAPI retrieval",
            "workspace_id": 1,
            "limit": 3,
        }
    ]

    assert [
        hit.chunk_id
        for hit in hits
    ] == [
        "chunk-b",
        "chunk-a",
    ]

    assert hits[0].dense_rank == 2
    assert hits[0].bm25_rank == 1
    assert hits[0].dense_score == 0.80
    assert hits[0].bm25_score == 20.0

    assert hits[1].dense_rank == 1
    assert hits[1].bm25_rank == 3


@pytest.mark.asyncio
async def test_search_preserves_single_route_candidates() -> None:
    dense = FakeDenseRetrievalService(
        [
            make_dense_hit(
                point_id=1,
                chunk_id="dense-only",
                score=0.9,
            ),
        ]
    )

    bm25 = FakeBM25RetrievalService(
        [
            make_bm25_hit(
                point_id=2,
                chunk_id="bm25-only",
                score=12.0,
            ),
        ]
    )

    service = HybridRetrievalService(
        dense_retrieval_service=dense,
        bm25_retrieval_service=bm25,
    )

    hits = await service.search(
        query="hybrid",
        workspace_id=1,
        candidate_limit=2,
        limit=2,
    )

    by_chunk_id = {
        hit.chunk_id: hit
        for hit in hits
    }

    assert set(by_chunk_id) == {
        "dense-only",
        "bm25-only",
    }

    assert (
        by_chunk_id["dense-only"].dense_rank
        == 1
    )
    assert (
        by_chunk_id["dense-only"].bm25_rank
        is None
    )
    assert (
        by_chunk_id["dense-only"].bm25_score
        is None
    )

    assert (
        by_chunk_id["bm25-only"].dense_rank
        is None
    )
    assert (
        by_chunk_id["bm25-only"].dense_score
        is None
    )
    assert (
        by_chunk_id["bm25-only"].bm25_rank
        == 1
    )


@pytest.mark.asyncio
async def test_search_rejects_conflicting_shared_identity() -> None:
    dense = FakeDenseRetrievalService(
        [
            make_dense_hit(
                point_id=1,
                chunk_id="shared-chunk",
                score=0.9,
                document_id=10,
            ),
        ]
    )

    bm25 = FakeBM25RetrievalService(
        [
            make_bm25_hit(
                point_id=999,
                chunk_id="shared-chunk",
                score=10.0,
                document_id=10,
            ),
        ]
    )

    service = HybridRetrievalService(
        dense_retrieval_service=dense,
        bm25_retrieval_service=bm25,
    )

    with pytest.raises(
        ValueError,
        match="conflicting identity",
    ):
        await service.search(
            query="identity",
            workspace_id=1,
            candidate_limit=5,
            limit=5,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "query",
        "workspace_id",
        "candidate_limit",
        "limit",
        "message",
    ),
    [
        (
            " ",
            1,
            20,
            5,
            "query must not be empty",
        ),
        (
            "query",
            0,
            20,
            5,
            "workspace_id",
        ),
        (
            "query",
            1,
            0,
            5,
            "candidate_limit",
        ),
        (
            "query",
            1,
            20,
            0,
            "limit",
        ),
        (
            "query",
            1,
            4,
            5,
            "candidate_limit",
        ),
    ],
)
async def test_search_validates_arguments(
    query: str,
    workspace_id: int,
    candidate_limit: int,
    limit: int,
    message: str,
) -> None:
    dense = FakeDenseRetrievalService([])
    bm25 = FakeBM25RetrievalService([])

    service = HybridRetrievalService(
        dense_retrieval_service=dense,
        bm25_retrieval_service=bm25,
    )

    with pytest.raises(
        ValueError,
        match=message,
    ):
        await service.search(
            query=query,
            workspace_id=workspace_id,
            candidate_limit=candidate_limit,
            limit=limit,
        )

    assert dense.calls == []
    assert bm25.calls == []
