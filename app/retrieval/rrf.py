from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class RRFFusedResult:
    """One chunk after reciprocal-rank fusion."""

    chunk_id: str
    score: float
    dense_rank: int | None
    bm25_rank: int | None


@dataclass
class _RRFAccumulator:
    score: float = 0.0
    dense_rank: int | None = None
    bm25_rank: int | None = None


def reciprocal_rank_fusion(
    *,
    dense_chunk_ids: Sequence[str],
    bm25_chunk_ids: Sequence[str],
    k: int = 60,
) -> list[RRFFusedResult]:
    """
    Fuse Dense and BM25 rankings using Reciprocal Rank Fusion.

    RRF(chunk) = sum(1 / (k + rank_i(chunk)))

    Only ranks are used. Original retriever scores are deliberately ignored.
    """

    if k <= 0:
        raise ValueError("k must be greater than zero")

    accumulators: dict[str, _RRFAccumulator] = {}

    _accumulate_ranking(
        chunk_ids=dense_chunk_ids,
        source="dense",
        k=k,
        accumulators=accumulators,
    )
    _accumulate_ranking(
        chunk_ids=bm25_chunk_ids,
        source="bm25",
        k=k,
        accumulators=accumulators,
    )

    results = [
        RRFFusedResult(
            chunk_id=chunk_id,
            score=accumulator.score,
            dense_rank=accumulator.dense_rank,
            bm25_rank=accumulator.bm25_rank,
        )
        for chunk_id, accumulator in accumulators.items()
    ]

    results.sort(
        key=lambda result: (
            -result.score,
            _best_rank(result),
            result.chunk_id,
        )
    )

    return results


def _accumulate_ranking(
    *,
    chunk_ids: Sequence[str],
    source: str,
    k: int,
    accumulators: dict[str, _RRFAccumulator],
) -> None:
    seen_chunk_ids: set[str] = set()

    for rank, raw_chunk_id in enumerate(
        chunk_ids,
        start=1,
    ):
        chunk_id = raw_chunk_id.strip()

        if not chunk_id:
            raise ValueError("chunk_id must not be empty")

        # A retriever should normally return unique chunks. If it does not,
        # the duplicate must not receive a second contribution from the
        # same retrieval route.
        if chunk_id in seen_chunk_ids:
            continue

        seen_chunk_ids.add(chunk_id)

        accumulator = accumulators.setdefault(
            chunk_id,
            _RRFAccumulator(),
        )

        accumulator.score += 1.0 / (k + rank)

        if source == "dense":
            accumulator.dense_rank = rank
        elif source == "bm25":
            accumulator.bm25_rank = rank
        else:
            raise ValueError(
                f"unsupported retrieval source: {source}"
            )


def _best_rank(
    result: RRFFusedResult,
) -> int:
    ranks = [
        rank
        for rank in (
            result.dense_rank,
            result.bm25_rank,
        )
        if rank is not None
    ]

    return min(ranks)