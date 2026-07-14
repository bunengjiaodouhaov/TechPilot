from typing import Protocol

from app.retrieval.dto import VectorPoint, VectorSearchHit


class VectorRepository(Protocol):
    """Contract for vector storage operations."""

    async def ensure_collection(self) -> None:
        """Ensure that the configured vector collection exists."""
        ...

    async def upsert_points(
        self,
        points: list[VectorPoint],
    ) -> None:
        """Insert or update vector points."""
        ...

    async def search(
        self,
        *,
        query_vector: list[float],
        workspace_id: int,
        limit: int,
    ) -> list[VectorSearchHit]:
        """Search similar vectors within one workspace."""
        ...
