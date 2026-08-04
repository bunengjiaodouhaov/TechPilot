from __future__ import annotations

from typing import Protocol

from app.retrieval.bm25_dto import BM25SearchHit
from app.retrieval.dto import VectorSearchHit
from app.retrieval.hybrid_dto import HybridSearchHit
from app.retrieval.rrf import reciprocal_rank_fusion


class DenseRetrievalProtocol(Protocol):
    async def search(
        self,
        *,
        query: str,
        workspace_id: int,
        limit: int = 5,
    ) -> list[VectorSearchHit]:
        ...


class BM25RetrievalProtocol(Protocol):
    async def search(
        self,
        *,
        query: str,
        workspace_id: int,
        limit: int = 5,
    ) -> list[BM25SearchHit]:
        ...


class HybridRetrievalService:
    """Fuse independent Dense and BM25 rankings with RRF."""

    def __init__(
        self,
        *,
        dense_retrieval_service: DenseRetrievalProtocol,
        bm25_retrieval_service: BM25RetrievalProtocol,
        rrf_k: int = 60,
    ) -> None:
        if rrf_k <= 0:
            raise ValueError("rrf_k must be greater than zero")

        self._dense_retrieval_service = dense_retrieval_service
        self._bm25_retrieval_service = bm25_retrieval_service
        self._rrf_k = rrf_k

    async def search(
        self,
        *,
        query: str,
        workspace_id: int,
        candidate_limit: int = 20,
        limit: int = 5,
    ) -> list[HybridSearchHit]:
        normalized_query = query.strip()

        if not normalized_query:
            raise ValueError("query must not be empty")

        if workspace_id <= 0:
            raise ValueError(
                "workspace_id must be greater than zero"
            )

        if candidate_limit <= 0:
            raise ValueError(
                "candidate_limit must be greater than zero"
            )

        if limit <= 0:
            raise ValueError(
                "limit must be greater than zero"
            )

        if candidate_limit < limit:
            raise ValueError(
                "candidate_limit must be greater than "
                "or equal to limit"
            )

        dense_hits = await self._dense_retrieval_service.search(
            query=normalized_query,
            workspace_id=workspace_id,
            limit=candidate_limit,
        )

        bm25_hits = await self._bm25_retrieval_service.search(
            query=normalized_query,
            workspace_id=workspace_id,
            limit=candidate_limit,
        )

        dense_by_chunk_id = self._first_dense_hits(
            hits=dense_hits,
        )
        bm25_by_chunk_id = self._first_bm25_hits(
            hits=bm25_hits,
        )

        fused_results = reciprocal_rank_fusion(
            dense_chunk_ids=[
                hit.payload.chunk_id
                for hit in dense_hits
            ],
            bm25_chunk_ids=[
                hit.chunk_id
                for hit in bm25_hits
            ],
            k=self._rrf_k,
        )

        results: list[HybridSearchHit] = []

        for fused in fused_results[:limit]:
            dense_hit = dense_by_chunk_id.get(
                fused.chunk_id
            )
            bm25_hit = bm25_by_chunk_id.get(
                fused.chunk_id
            )

            if dense_hit is not None and bm25_hit is not None:
                self._validate_shared_identity(
                    dense_hit=dense_hit,
                    bm25_hit=bm25_hit,
                )

            results.append(
                self._build_hit(
                    chunk_id=fused.chunk_id,
                    rrf_score=fused.score,
                    dense_rank=fused.dense_rank,
                    bm25_rank=fused.bm25_rank,
                    dense_hit=dense_hit,
                    bm25_hit=bm25_hit,
                )
            )

        return results

    @staticmethod
    def _first_dense_hits(
        *,
        hits: list[VectorSearchHit],
    ) -> dict[str, VectorSearchHit]:
        by_chunk_id: dict[str, VectorSearchHit] = {}

        for hit in hits:
            by_chunk_id.setdefault(
                hit.payload.chunk_id,
                hit,
            )

        return by_chunk_id

    @staticmethod
    def _first_bm25_hits(
        *,
        hits: list[BM25SearchHit],
    ) -> dict[str, BM25SearchHit]:
        by_chunk_id: dict[str, BM25SearchHit] = {}

        for hit in hits:
            by_chunk_id.setdefault(
                hit.chunk_id,
                hit,
            )

        return by_chunk_id

    @staticmethod
    def _validate_shared_identity(
        *,
        dense_hit: VectorSearchHit,
        bm25_hit: BM25SearchHit,
    ) -> None:
        dense_identity = (
            dense_hit.point_id,
            dense_hit.payload.workspace_id,
            dense_hit.payload.document_id,
            dense_hit.payload.chunk_id,
            dense_hit.payload.chunk_index,
        )

        bm25_identity = (
            bm25_hit.point_id,
            bm25_hit.workspace_id,
            bm25_hit.document_id,
            bm25_hit.chunk_id,
            bm25_hit.chunk_index,
        )

        if dense_identity != bm25_identity:
            raise ValueError(
                "Dense and BM25 returned conflicting "
                f"identity for chunk_id={bm25_hit.chunk_id}"
            )

    @staticmethod
    def _build_hit(
        *,
        chunk_id: str,
        rrf_score: float,
        dense_rank: int | None,
        bm25_rank: int | None,
        dense_hit: VectorSearchHit | None,
        bm25_hit: BM25SearchHit | None,
    ) -> HybridSearchHit:
        if dense_hit is None and bm25_hit is None:
            raise ValueError(
                f"missing source hit for chunk_id={chunk_id}"
            )

        if dense_hit is not None:
            payload = dense_hit.payload

            return HybridSearchHit(
                point_id=dense_hit.point_id,
                rrf_score=rrf_score,
                workspace_id=payload.workspace_id,
                document_id=payload.document_id,
                chunk_id=payload.chunk_id,
                chunk_index=payload.chunk_index,
                section=payload.section,
                document_name=payload.document_name,
                source_type=payload.source_type,
                page_start=payload.page_start,
                page_end=payload.page_end,
                dense_rank=dense_rank,
                dense_score=dense_hit.score,
                bm25_rank=bm25_rank,
                bm25_score=(
                    bm25_hit.score
                    if bm25_hit is not None
                    else None
                ),
            )

        assert bm25_hit is not None

        return HybridSearchHit(
            point_id=bm25_hit.point_id,
            rrf_score=rrf_score,
            workspace_id=bm25_hit.workspace_id,
            document_id=bm25_hit.document_id,
            chunk_id=bm25_hit.chunk_id,
            chunk_index=bm25_hit.chunk_index,
            section=bm25_hit.section,
            document_name=bm25_hit.document_name,
            source_type=bm25_hit.source_type,
            page_start=bm25_hit.page_start,
            page_end=bm25_hit.page_end,
            dense_rank=dense_rank,
            dense_score=None,
            bm25_rank=bm25_rank,
            bm25_score=bm25_hit.score,
        )
