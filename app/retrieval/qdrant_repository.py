from dataclasses import asdict
from typing import Any, Mapping, cast

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qdrant_models

from app.retrieval.dto import (
    ChunkVectorPayload,
    VectorPoint,
    VectorSearchHit,
)
from app.retrieval.repository import VectorRepository


class QdrantRepository(VectorRepository):
    """Qdrant implementation of the vector repository contract."""

    def __init__(
        self,
        *,
        qdrant_url: str,
        collection_name: str,
        dimension: int,
        client: AsyncQdrantClient | None = None,
    ) -> None:
        if not qdrant_url.strip():
            raise ValueError("qdrant_url must not be empty")

        if not collection_name.strip():
            raise ValueError("collection_name must not be empty")

        if dimension <= 0:
            raise ValueError("dimension must be greater than zero")

        self._collection_name = collection_name
        self._dimension = dimension

        self._client = client or AsyncQdrantClient(
            url=qdrant_url,
        )

    async def ensure_collection(self) -> None:
        """Create the configured collection when it does not yet exist."""

        exists = await self._client.collection_exists(
            collection_name=self._collection_name,
        )

        if exists:
            return

        await self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config=qdrant_models.VectorParams(
                size=self._dimension,
                distance=qdrant_models.Distance.COSINE,
            ),
        )

    async def upsert_points(
        self,
        points: list[VectorPoint],
    ) -> None:
        """Insert new points or replace points with the same point ID."""

        if not points:
            return

        qdrant_points = [
            self._to_qdrant_point(point)
            for point in points
        ]

        await self._client.upsert(
            collection_name=self._collection_name,
            points=qdrant_points,
            wait=True,
        )

    async def search(
        self,
        *,
        query_vector: list[float],
        workspace_id: int,
        limit: int,
    ) -> list[VectorSearchHit]:
        """Search the most similar points inside one workspace."""

        if not query_vector:
            raise ValueError("query_vector must not be empty")

        if workspace_id <= 0:
            raise ValueError("workspace_id must be greater than zero")

        if limit <= 0:
            raise ValueError("limit must be greater than zero")

        response = await self._client.query_points(
            collection_name=self._collection_name,
            query=query_vector,
            query_filter=qdrant_models.Filter(
                must=[
                    qdrant_models.FieldCondition(
                        key="workspace_id",
                        match=qdrant_models.MatchValue(
                            value=workspace_id,
                        ),
                    ),
                ],
            ),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )

        return [
            self._to_search_hit(point)
            for point in response.points
        ]

    @staticmethod
    def _to_qdrant_point(
        point: VectorPoint,
    ) -> qdrant_models.PointStruct:
        """Convert an internal VectorPoint DTO into a Qdrant SDK object."""

        return qdrant_models.PointStruct(
            id=point.point_id,
            vector=point.vector,
            payload=asdict(point.payload),
        )

    @staticmethod
    def _to_search_hit(
        point: Any,
    ) -> VectorSearchHit:
        """Convert a Qdrant scored point into an internal search DTO."""

        if not isinstance(point.id, int):
            raise ValueError(
                "Qdrant point ID must be an integer",
            )

        payload = QdrantRepository._to_chunk_payload(
            point.payload,
        )

        return VectorSearchHit(
            point_id=point.id,
            score=float(point.score),
            payload=payload,
        )

    @staticmethod
    def _to_chunk_payload(
        payload: Mapping[str, Any] | None,
    ) -> ChunkVectorPayload:
        """Convert Qdrant payload data into ChunkVectorPayload."""

        if payload is None:
            raise ValueError(
                "Qdrant search result is missing payload",
            )

        return ChunkVectorPayload(
            workspace_id=cast(int, payload["workspace_id"]),
            document_id=cast(int, payload["document_id"]),
            chunk_id=cast(str, payload["chunk_id"]),
            chunk_index=cast(int, payload["chunk_index"]),
            section=cast(str | None, payload.get("section")),
            document_name=cast(str, payload["document_name"]),
            source_type=cast(str, payload["source_type"]),
            page_start=cast(int | None, payload.get("page_start")),
            page_end=cast(int | None, payload.get("page_end")),
        )
