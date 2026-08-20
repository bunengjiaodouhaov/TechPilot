from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.repository.ast_service import PythonSymbolKind
from app.repository.code_index import CodeSearchHit
from app.repository.code_retrieval import CodeRetrievalService


class CodeHybridIdentityError(ValueError):
    """Raised when two retrieval routes disagree about one chunk identity."""


@dataclass(frozen=True, slots=True)
class CodeHybridSearchHit:
    chunk_id: str
    file_path: str
    symbol: str
    kind: PythonSymbolKind
    line_start: int
    line_end: int
    score: float
    keyword_rank: int | None
    dense_rank: int | None
    keyword_score: float | None
    dense_score: float | None


@dataclass(slots=True)
class _HybridAccumulator:
    hit: CodeSearchHit
    score: float = 0.0
    keyword_rank: int | None = None
    dense_rank: int | None = None
    keyword_score: float | None = None
    dense_score: float | None = None


class CodeHybridRetrievalService:
    """Fuse existing keyword and dense code retrievers with rank-only RRF."""

    def __init__(
        self,
        *,
        retrieval_service: CodeRetrievalService,
        rrf_k: int = 60,
        candidate_multiplier: int = 4,
        minimum_candidate_limit: int = 20,
    ) -> None:
        if rrf_k <= 0:
            raise ValueError("rrf_k must be greater than zero")
        if candidate_multiplier <= 0:
            raise ValueError("candidate_multiplier must be greater than zero")
        if minimum_candidate_limit <= 0:
            raise ValueError("minimum_candidate_limit must be greater than zero")

        self._retrieval_service = retrieval_service
        self._rrf_k = rrf_k
        self._candidate_multiplier = candidate_multiplier
        self._minimum_candidate_limit = minimum_candidate_limit

    async def search(
        self,
        *,
        query: str,
        limit: int = 10,
    ) -> list[CodeHybridSearchHit]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty")
        if limit <= 0:
            raise ValueError("limit must be greater than zero")

        candidate_limit = max(
            limit * self._candidate_multiplier,
            self._minimum_candidate_limit,
        )

        keyword_hits, dense_hits = await asyncio.gather(
            self._retrieval_service.search_keyword(
                query=normalized_query,
                limit=candidate_limit,
            ),
            self._retrieval_service.search_dense(
                query=normalized_query,
                limit=candidate_limit,
            ),
        )

        accumulators: dict[str, _HybridAccumulator] = {}
        self._accumulate(
            hits=keyword_hits,
            channel="keyword",
            accumulators=accumulators,
        )
        self._accumulate(
            hits=dense_hits,
            channel="dense",
            accumulators=accumulators,
        )

        results = [
            CodeHybridSearchHit(
                chunk_id=chunk_id,
                file_path=accumulator.hit.file_path,
                symbol=accumulator.hit.symbol,
                kind=accumulator.hit.kind,
                line_start=accumulator.hit.line_start,
                line_end=accumulator.hit.line_end,
                score=accumulator.score,
                keyword_rank=accumulator.keyword_rank,
                dense_rank=accumulator.dense_rank,
                keyword_score=accumulator.keyword_score,
                dense_score=accumulator.dense_score,
            )
            for chunk_id, accumulator in accumulators.items()
        ]
        results.sort(
            key=lambda result: (
                -result.score,
                self._best_rank(result),
                result.file_path,
                result.line_start,
                result.symbol,
                result.chunk_id,
            )
        )
        return results[:limit]

    def _accumulate(
        self,
        *,
        hits: list[CodeSearchHit],
        channel: str,
        accumulators: dict[str, _HybridAccumulator],
    ) -> None:
        seen_chunk_ids: set[str] = set()

        for rank, hit in enumerate(hits, start=1):
            if hit.chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(hit.chunk_id)

            accumulator = accumulators.get(hit.chunk_id)
            if accumulator is None:
                accumulator = _HybridAccumulator(hit=hit)
                accumulators[hit.chunk_id] = accumulator
            else:
                self._assert_same_identity(
                    first=accumulator.hit,
                    second=hit,
                )

            accumulator.score += 1.0 / (self._rrf_k + rank)

            if channel == "keyword":
                accumulator.keyword_rank = rank
                accumulator.keyword_score = hit.score
            elif channel == "dense":
                accumulator.dense_rank = rank
                accumulator.dense_score = hit.score
            else:
                raise ValueError(f"unsupported retrieval channel: {channel}")

    @staticmethod
    def _assert_same_identity(
        *,
        first: CodeSearchHit,
        second: CodeSearchHit,
    ) -> None:
        first_identity = (
            first.file_path,
            first.symbol,
            first.kind,
            first.line_start,
            first.line_end,
        )
        second_identity = (
            second.file_path,
            second.symbol,
            second.kind,
            second.line_start,
            second.line_end,
        )
        if first_identity != second_identity:
            raise CodeHybridIdentityError(
                "retrieval routes disagree about chunk identity"
            )

    @staticmethod
    def _best_rank(result: CodeHybridSearchHit) -> int:
        ranks = [
            rank
            for rank in (result.keyword_rank, result.dense_rank)
            if rank is not None
        ]
        return min(ranks)
