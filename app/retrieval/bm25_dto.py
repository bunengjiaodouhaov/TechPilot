from dataclasses import dataclass


@dataclass(frozen=True)
class BM25Chunk:
    """One PostgreSQL chunk eligible for BM25 retrieval."""

    point_id: int
    workspace_id: int
    document_id: int
    chunk_id: str
    chunk_index: int
    section: str | None
    document_name: str
    source_type: str
    page_start: int | None
    page_end: int | None
    text: str


@dataclass(frozen=True)
class BM25SearchHit:
    """One BM25 retrieval result."""

    point_id: int
    score: float
    workspace_id: int
    document_id: int
    chunk_id: str
    chunk_index: int
    section: str | None
    document_name: str
    source_type: str
    page_start: int | None
    page_end: int | None
