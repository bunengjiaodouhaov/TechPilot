from dataclasses import dataclass


@dataclass(frozen=True)
class HybridSearchHit:
    """One chunk returned after Dense + BM25 RRF fusion."""

    point_id: int
    rrf_score: float

    workspace_id: int
    document_id: int
    chunk_id: str
    chunk_index: int

    section: str | None
    document_name: str
    source_type: str
    page_start: int | None
    page_end: int | None

    dense_rank: int | None
    dense_score: float | None

    bm25_rank: int | None
    bm25_score: float | None
