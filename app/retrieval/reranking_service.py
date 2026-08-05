from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol

from app.answering.dto import StoredChunk
from app.retrieval.hybrid_dto import HybridSearchHit
from app.retrieval.reranker import RerankerProvider
from app.retrieval.reranker_dto import RerankCandidate, RerankedSearchHit


class HybridRetrievalProtocol(Protocol):
    async def search(
        self,
        *,
        query: str,
        workspace_id: int,
        candidate_limit: int = 20,
        limit: int = 5,
    ) -> list[HybridSearchHit]:
        ...


class ChunkRepositoryProtocol(Protocol):
    async def get_by_ids(
        self,
        *,
        chunk_ids: Sequence[int],
        workspace_id: int,
    ) -> dict[int, StoredChunk]:
        ...


class RerankingDataConsistencyError(RuntimeError):
    """Raised when a Hybrid candidate cannot be resolved consistently in PostgreSQL."""


class RerankingService:
    """Rerank a bounded Hybrid candidate pool with authoritative chunk text."""

    def __init__(
        self,
        *,
        hybrid_retrieval_service: HybridRetrievalProtocol,
        chunk_repository: ChunkRepositoryProtocol,
        reranker_provider: RerankerProvider,
    ) -> None:
        self._hybrid_retrieval_service = hybrid_retrieval_service
        self._chunk_repository = chunk_repository
        self._reranker_provider = reranker_provider

    async def search(
        self,
        *,
        query: str,
        workspace_id: int,
        candidate_limit: int = 20,
        rerank_depth: int = 20,
        limit: int = 5,
    ) -> list[RerankedSearchHit]:
        normalized_query = query.strip()

        if not normalized_query:
            raise ValueError("query must not be empty")
        if workspace_id <= 0:
            raise ValueError("workspace_id must be greater than zero")
        if candidate_limit <= 0:
            raise ValueError("candidate_limit must be greater than zero")
        if rerank_depth <= 0:
            raise ValueError("rerank_depth must be greater than zero")
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        if rerank_depth > candidate_limit * 2:
            raise ValueError(
                "rerank_depth must not exceed twice candidate_limit"
            )
        if rerank_depth < limit:
            raise ValueError(
                "rerank_depth must be greater than or equal to limit"
            )

        hybrid_hits = await self._hybrid_retrieval_service.search(
            query=normalized_query,
            workspace_id=workspace_id,
            candidate_limit=candidate_limit,
            limit=rerank_depth,
        )

        if not hybrid_hits:
            return []

        stored_chunks = await self._chunk_repository.get_by_ids(
            chunk_ids=[hit.point_id for hit in hybrid_hits],
            workspace_id=workspace_id,
        )

        candidates = self._build_candidates(
            hybrid_hits=hybrid_hits,
            stored_chunks=stored_chunks,
        )

        scores = await asyncio.to_thread(
            self._reranker_provider.score,
            query=normalized_query,
            documents=[candidate.text for candidate in candidates],
        )

        if len(scores) != len(candidates):
            raise ValueError(
                "reranker output count does not match candidate count: "
                f"expected {len(candidates)}, got {len(scores)}"
            )

        scored = list(zip(candidates, scores, strict=True))
        scored.sort(
            key=lambda item: (
                -item[1],
                item[0].original_rank,
                item[0].hybrid_hit.chunk_id,
            )
        )

        return [
            RerankedSearchHit(
                hybrid_hit=candidate.hybrid_hit,
                reranker_score=float(score),
                original_rank=candidate.original_rank,
                rerank_rank=rerank_rank,
            )
            for rerank_rank, (candidate, score) in enumerate(
                scored[:limit],
                start=1,
            )
        ]

    @staticmethod
    def _build_candidates(
        *,
        hybrid_hits: Sequence[HybridSearchHit],
        stored_chunks: dict[int, StoredChunk],
    ) -> list[RerankCandidate]:
        missing_point_ids = [
            hit.point_id
            for hit in hybrid_hits
            if hit.point_id not in stored_chunks
        ]

        if missing_point_ids:
            raise RerankingDataConsistencyError(
                "Hybrid candidates are missing from PostgreSQL: "
                + ", ".join(str(point_id) for point_id in missing_point_ids)
            )

        candidates: list[RerankCandidate] = []

        for original_rank, hit in enumerate(hybrid_hits, start=1):
            stored_chunk = stored_chunks[hit.point_id]
            RerankingService._validate_identity(
                hybrid_hit=hit,
                stored_chunk=stored_chunk,
            )
            candidates.append(
                RerankCandidate(
                    hybrid_hit=hit,
                    text=stored_chunk.text,
                    original_rank=original_rank,
                )
            )

        return candidates

    @staticmethod
    def _validate_identity(
        *,
        hybrid_hit: HybridSearchHit,
        stored_chunk: StoredChunk,
    ) -> None:
        hybrid_identity = (
            hybrid_hit.point_id,
            hybrid_hit.document_id,
            hybrid_hit.chunk_id,
            hybrid_hit.chunk_index,
            hybrid_hit.document_name,
        )
        stored_identity = (
            stored_chunk.chunk_db_id,
            stored_chunk.document_id,
            stored_chunk.chunk_id,
            stored_chunk.chunk_index,
            stored_chunk.document_name,
        )

        if hybrid_identity != stored_identity:
            raise RerankingDataConsistencyError(
                "Hybrid/PostgreSQL identity mismatch for "
                f"point_id={hybrid_hit.point_id}"
            )
