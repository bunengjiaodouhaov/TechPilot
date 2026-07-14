from dataclasses import dataclass


@dataclass(frozen=True)
class ChunkVectorPayload:
    """Metadata stored alongside a chunk vector in Qdrant."""

    workspace_id: int
    document_id: int

    chunk_id: str
    chunk_index: int

    section: str | None

    document_name: str
    source_type: str

    page_start: int | None
    page_end: int | None


@dataclass(frozen=True)
class VectorPoint:
    """Storage-independent representation of one vector point."""

    point_id: int
    vector: list[float]
    payload: ChunkVectorPayload


@dataclass(frozen=True)
class VectorSearchHit:
    """Storage-independent result returned by vector search."""

    point_id: int
    score: float
    payload: ChunkVectorPayload
