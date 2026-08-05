from __future__ import annotations

from dataclasses import dataclass

from app.retrieval.hybrid_dto import HybridSearchHit


@dataclass(frozen=True)
class RerankCandidate:
    """Hybrid candidate enriched with authoritative PostgreSQL text."""

    hybrid_hit: HybridSearchHit
    text: str
    original_rank: int


@dataclass(frozen=True)
class RerankedSearchHit:
    """One Hybrid candidate after Cross Encoder reranking."""

    hybrid_hit: HybridSearchHit
    reranker_score: float
    original_rank: int
    rerank_rank: int
