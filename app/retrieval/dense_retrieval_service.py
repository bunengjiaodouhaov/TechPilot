import asyncio

from app.retrieval.dto import VectorSearchHit
from app.retrieval.embedding import EmbeddingProvider
from app.retrieval.repository import VectorRepository


class DenseRetrievalService:
    """Coordinate query embedding and dense vector retrieval."""

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        vector_repository: VectorRepository,
    ) -> None:
        self._embedding_provider = embedding_provider
        self._vector_repository = vector_repository

    async def search(
        self,
        *,
        query: str,
        workspace_id: int,
        limit: int = 5,
    ) -> list[VectorSearchHit]:
        """Return the most similar vector hits inside one workspace."""

        normalized_query = query.strip()

        if not normalized_query:
            raise ValueError("query must not be empty")

        if workspace_id <= 0:
            raise ValueError("workspace_id must be greater than zero")

        if limit <= 0:
            raise ValueError("limit must be greater than zero")

        query_vector = await asyncio.to_thread(
            self._embedding_provider.embed_query,
            normalized_query,
        )

        return await self._vector_repository.search(
            query_vector=query_vector,
            workspace_id=workspace_id,
            limit=limit,
        )
