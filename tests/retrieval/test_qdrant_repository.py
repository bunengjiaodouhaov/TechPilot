from types import SimpleNamespace
from typing import Any

import pytest
from qdrant_client import models as qdrant_models

from app.retrieval.dto import ChunkVectorPayload, VectorPoint
from app.retrieval.qdrant_repository import QdrantRepository


class FakeAsyncQdrantClient:
    def __init__(
        self,
        *,
        collection_exists: bool = False,
        query_points: list[Any] | None = None,
    ) -> None:
        self.collection_exists_result = collection_exists
        self.query_points_result = query_points or []

        self.collection_exists_calls: list[dict[str, Any]] = []
        self.create_collection_calls: list[dict[str, Any]] = []
        self.upsert_calls: list[dict[str, Any]] = []
        self.query_points_calls: list[dict[str, Any]] = []

    async def collection_exists(
        self,
        **kwargs: Any,
    ) -> bool:
        self.collection_exists_calls.append(kwargs)
        return self.collection_exists_result

    async def create_collection(
        self,
        **kwargs: Any,
    ) -> None:
        self.create_collection_calls.append(kwargs)

    async def upsert(
        self,
        **kwargs: Any,
    ) -> None:
        self.upsert_calls.append(kwargs)

    async def query_points(
        self,
        **kwargs: Any,
    ) -> Any:
        self.query_points_calls.append(kwargs)
        return SimpleNamespace(
            points=self.query_points_result,
        )


def make_payload() -> ChunkVectorPayload:
    return ChunkVectorPayload(
        workspace_id=1,
        document_id=10,
        chunk_id="document-10-chunk-0",
        chunk_index=0,
        section="Introduction",
        document_name="example.pdf",
        source_type="pdf",
        page_start=1,
        page_end=2,
    )


def make_repository(
    client: FakeAsyncQdrantClient,
) -> QdrantRepository:
    return QdrantRepository(
        qdrant_url="http://localhost:6333",
        collection_name="techpilot_chunks",
        dimension=768,
        client=client,  # type: ignore[arg-type]
    )


def test_repository_rejects_invalid_configuration() -> None:
    client = FakeAsyncQdrantClient()

    with pytest.raises(ValueError):
        QdrantRepository(
            qdrant_url="",
            collection_name="techpilot_chunks",
            dimension=768,
            client=client,  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError):
        QdrantRepository(
            qdrant_url="http://localhost:6333",
            collection_name="",
            dimension=768,
            client=client,  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError):
        QdrantRepository(
            qdrant_url="http://localhost:6333",
            collection_name="techpilot_chunks",
            dimension=0,
            client=client,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_ensure_collection_does_nothing_when_collection_exists() -> None:
    client = FakeAsyncQdrantClient(
        collection_exists=True,
    )
    repository = make_repository(client)

    await repository.ensure_collection()

    assert client.collection_exists_calls == [
        {
            "collection_name": "techpilot_chunks",
        },
    ]
    assert client.create_collection_calls == []


@pytest.mark.asyncio
async def test_ensure_collection_creates_missing_collection() -> None:
    client = FakeAsyncQdrantClient(
        collection_exists=False,
    )
    repository = make_repository(client)

    await repository.ensure_collection()

    assert len(client.create_collection_calls) == 1

    call = client.create_collection_calls[0]

    assert call["collection_name"] == "techpilot_chunks"

    vector_config = call["vectors_config"]

    assert isinstance(
        vector_config,
        qdrant_models.VectorParams,
    )
    assert vector_config.size == 768
    assert vector_config.distance == qdrant_models.Distance.COSINE


@pytest.mark.asyncio
async def test_upsert_points_converts_dto_to_qdrant_point() -> None:
    client = FakeAsyncQdrantClient()
    repository = make_repository(client)

    point = VectorPoint(
        point_id=100,
        vector=[0.1, 0.2, 0.3],
        payload=make_payload(),
    )

    await repository.upsert_points([point])

    assert len(client.upsert_calls) == 1

    call = client.upsert_calls[0]

    assert call["collection_name"] == "techpilot_chunks"
    assert call["wait"] is True
    assert len(call["points"]) == 1

    qdrant_point = call["points"][0]

    assert isinstance(
        qdrant_point,
        qdrant_models.PointStruct,
    )
    assert qdrant_point.id == 100
    assert qdrant_point.vector == [0.1, 0.2, 0.3]
    assert qdrant_point.payload == {
        "workspace_id": 1,
        "document_id": 10,
        "chunk_id": "document-10-chunk-0",
        "chunk_index": 0,
        "section": "Introduction",
        "document_name": "example.pdf",
        "source_type": "pdf",
        "page_start": 1,
        "page_end": 2,
    }


@pytest.mark.asyncio
async def test_upsert_points_skips_empty_input() -> None:
    client = FakeAsyncQdrantClient()
    repository = make_repository(client)

    await repository.upsert_points([])

    assert client.upsert_calls == []


@pytest.mark.asyncio
async def test_search_filters_workspace_and_returns_dto() -> None:
    payload = {
        "workspace_id": 1,
        "document_id": 10,
        "chunk_id": "document-10-chunk-0",
        "chunk_index": 0,
        "section": "Introduction",
        "document_name": "example.pdf",
        "source_type": "pdf",
        "page_start": 1,
        "page_end": 2,
    }

    client = FakeAsyncQdrantClient(
        query_points=[
            SimpleNamespace(
                id=100,
                score=0.87,
                payload=payload,
            ),
        ],
    )
    repository = make_repository(client)

    hits = await repository.search(
        query_vector=[0.1, 0.2, 0.3],
        workspace_id=1,
        limit=5,
    )

    assert len(client.query_points_calls) == 1

    call = client.query_points_calls[0]

    assert call["collection_name"] == "techpilot_chunks"
    assert call["query"] == [0.1, 0.2, 0.3]
    assert call["limit"] == 5
    assert call["with_payload"] is True
    assert call["with_vectors"] is False

    query_filter = call["query_filter"]

    assert isinstance(
        query_filter,
        qdrant_models.Filter,
    )
    assert query_filter.must is not None
    assert len(query_filter.must) == 1

    workspace_condition = query_filter.must[0]

    assert isinstance(
        workspace_condition,
        qdrant_models.FieldCondition,
    )
    assert workspace_condition.key == "workspace_id"
    assert workspace_condition.match is not None
    assert workspace_condition.match.value == 1

    assert len(hits) == 1
    assert hits[0].point_id == 100
    assert hits[0].score == 0.87
    assert hits[0].payload == make_payload()


@pytest.mark.asyncio
async def test_search_rejects_invalid_arguments() -> None:
    client = FakeAsyncQdrantClient()
    repository = make_repository(client)

    with pytest.raises(ValueError):
        await repository.search(
            query_vector=[],
            workspace_id=1,
            limit=5,
        )

    with pytest.raises(ValueError):
        await repository.search(
            query_vector=[0.1],
            workspace_id=0,
            limit=5,
        )

    with pytest.raises(ValueError):
        await repository.search(
            query_vector=[0.1],
            workspace_id=1,
            limit=0,
        )

    assert client.query_points_calls == []


@pytest.mark.asyncio
async def test_search_rejects_result_without_payload() -> None:
    client = FakeAsyncQdrantClient(
        query_points=[
            SimpleNamespace(
                id=100,
                score=0.87,
                payload=None,
            ),
        ],
    )
    repository = make_repository(client)

    with pytest.raises(
        ValueError,
        match="missing payload",
    ):
        await repository.search(
            query_vector=[0.1, 0.2],
            workspace_id=1,
            limit=5,
        )
