from typing import Any

import pytest

from app.retrieval.dense_retrieval_service import DenseRetrievalService
from app.retrieval.dto import ChunkVectorPayload, VectorSearchHit


class FakeEmbeddingProvider:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]

    def embed_query(
        self,
        text: str,
    ) -> list[float]:
        self.queries.append(text)
        return [0.1, 0.2, 0.3]


class FakeVectorRepository:
    def __init__(
        self,
        *,
        hits: list[VectorSearchHit] | None = None,
    ) -> None:
        self.hits = hits or []
        self.search_calls: list[dict[str, Any]] = []

    async def ensure_collection(self) -> None:
        return None

    async def upsert_points(
        self,
        points: list[Any],
    ) -> None:
        return None

    async def search(
        self,
        *,
        query_vector: list[float],
        workspace_id: int,
        limit: int,
    ) -> list[VectorSearchHit]:
        self.search_calls.append(
            {
                "query_vector": query_vector,
                "workspace_id": workspace_id,
                "limit": limit,
            }
        )
        return self.hits


def make_hit() -> VectorSearchHit:
    return VectorSearchHit(
        point_id=100,
        score=0.87,
        payload=ChunkVectorPayload(
            workspace_id=1,
            document_id=10,
            chunk_id="document-10-chunk-0",
            chunk_index=0,
            section="Introduction",
            document_name="example.pdf",
            source_type="pdf",
            page_start=1,
            page_end=2,
        ),
    )


@pytest.mark.asyncio
async def test_search_embeds_normalized_query_and_calls_repository() -> None:
    embedding_provider = FakeEmbeddingProvider()
    vector_repository = FakeVectorRepository(
        hits=[make_hit()],
    )

    service = DenseRetrievalService(
        embedding_provider=embedding_provider,
        vector_repository=vector_repository,
    )

    hits = await service.search(
        query="  FastAPI 怎么启动？  ",
        workspace_id=1,
        limit=5,
    )

    assert embedding_provider.queries == [
        "FastAPI 怎么启动？",
    ]

    assert vector_repository.search_calls == [
        {
            "query_vector": [0.1, 0.2, 0.3],
            "workspace_id": 1,
            "limit": 5,
        },
    ]

    assert hits == [make_hit()]


@pytest.mark.asyncio
async def test_search_uses_default_limit() -> None:
    embedding_provider = FakeEmbeddingProvider()
    vector_repository = FakeVectorRepository()

    service = DenseRetrievalService(
        embedding_provider=embedding_provider,
        vector_repository=vector_repository,
    )

    await service.search(
        query="Docker Compose",
        workspace_id=1,
    )

    assert vector_repository.search_calls[0]["limit"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "workspace_id", "limit", "message"),
    [
        ("", 1, 5, "query must not be empty"),
        ("   ", 1, 5, "query must not be empty"),
        ("valid query", 0, 5, "workspace_id must be greater than zero"),
        ("valid query", -1, 5, "workspace_id must be greater than zero"),
        ("valid query", 1, 0, "limit must be greater than zero"),
        ("valid query", 1, -1, "limit must be greater than zero"),
    ],
)
async def test_search_rejects_invalid_arguments(
    query: str,
    workspace_id: int,
    limit: int,
    message: str,
) -> None:
    embedding_provider = FakeEmbeddingProvider()
    vector_repository = FakeVectorRepository()

    service = DenseRetrievalService(
        embedding_provider=embedding_provider,
        vector_repository=vector_repository,
    )

    with pytest.raises(ValueError, match=message):
        await service.search(
            query=query,
            workspace_id=workspace_id,
            limit=limit,
        )

    assert embedding_provider.queries == []
    assert vector_repository.search_calls == []
