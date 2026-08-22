from __future__ import annotations

from typing import Protocol

from app.retrieval.dto import ChunkVectorPayload, VectorSearchHit
from app.retrieval.reranker_dto import RerankedSearchHit


class RerankingSearchProtocol(Protocol):
    async def search(
        self,
        *,
        query: str,
        workspace_id: int,
        candidate_limit: int = 20,
        rerank_depth: int = 20,
        limit: int = 5,
    ) -> list[RerankedSearchHit]:
        ...


class AnswerRetrievalAdapter:
    """Expose the final Hybrid + CrossEncoder stack as VectorSearchHit search."""

    def __init__(
        self,
        *,
        reranking_service: RerankingSearchProtocol,
        candidate_limit: int = 40,
        rerank_depth: int = 20,
    ) -> None:
        if candidate_limit <= 0:
            raise ValueError("candidate_limit must be greater than zero")
        if rerank_depth <= 0:
            raise ValueError("rerank_depth must be greater than zero")
        if rerank_depth > candidate_limit * 2:
            raise ValueError(
                "rerank_depth must not exceed twice candidate_limit"
            )

        self._reranking_service = reranking_service
        self._candidate_limit = candidate_limit
        self._rerank_depth = rerank_depth

    async def search(
        self,
        *,
        query: str,
        workspace_id: int,
        limit: int = 5,
    ) -> list[VectorSearchHit]:
        if limit <= 0:
            raise ValueError("limit must be greater than zero")

        candidate_limit = max(self._candidate_limit, limit)
        rerank_depth = max(self._rerank_depth, limit)

        hits = await self._reranking_service.search(
            query=query,
            workspace_id=workspace_id,
            candidate_limit=candidate_limit,
            rerank_depth=rerank_depth,
            limit=limit,
        )

        return [
            self._to_vector_hit(hit)
            for hit in hits
        ]

    @staticmethod
    def _to_vector_hit(
        hit: RerankedSearchHit,
    ) -> VectorSearchHit:
        source = hit.hybrid_hit
        return VectorSearchHit(
            point_id=source.point_id,
            score=float(hit.reranker_score),
            payload=ChunkVectorPayload(
                workspace_id=source.workspace_id,
                document_id=source.document_id,
                chunk_id=source.chunk_id,
                chunk_index=source.chunk_index,
                section=source.section,
                document_name=source.document_name,
                source_type=source.source_type,
                page_start=source.page_start,
                page_end=source.page_end,
            ),
        )
