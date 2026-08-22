from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.retrieval.indexing_service import IndexingService


class FakeEmbeddingProvider:
    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        self.batches.append(list(texts))
        return [
            [float(index), 1.0]
            for index, _ in enumerate(texts)
        ]


class FakeVectorRepository:
    def __init__(
        self,
        *,
        fail_on_upsert_call: int | None = None,
    ) -> None:
        self.fail_on_upsert_call = fail_on_upsert_call
        self.ensure_calls = 0
        self.upsert_calls: list[list] = []
        self.delete_calls: list[tuple[int, int]] = []

    async def ensure_collection(self) -> None:
        self.ensure_calls += 1

    async def upsert_points(self, points: list) -> None:
        self.upsert_calls.append(list(points))
        if (
            self.fail_on_upsert_call is not None
            and len(self.upsert_calls)
            == self.fail_on_upsert_call
        ):
            raise RuntimeError("simulated qdrant failure")

    async def delete_document_points(
        self,
        *,
        workspace_id: int,
        document_id: int,
    ) -> None:
        self.delete_calls.append(
            (workspace_id, document_id)
        )


def _document():
    return SimpleNamespace(
        id=70,
        workspace_id=12,
        name="large.pdf",
        file_type="pdf",
    )


def _chunks(count: int):
    return [
        SimpleNamespace(
            id=1000 + index,
            document_id=70,
            chunk_id=f"chunk-{index}",
            chunk_index=index,
            text=f"chunk text {index}",
            section=None,
            page_start=index + 1,
            page_end=index + 1,
        )
        for index in range(count)
    ]


@pytest.mark.asyncio
async def test_indexes_in_bounded_batches() -> None:
    embedding = FakeEmbeddingProvider()
    repository = FakeVectorRepository()
    service = IndexingService(
        embedding_provider=embedding,
        vector_repository=repository,
        batch_size=2,
    )

    result = await service.index_document(
        document=_document(),
        chunks=_chunks(5),
    )

    assert result.indexed_chunk_count == 5
    assert repository.ensure_calls == 1
    assert [len(batch) for batch in repository.upsert_calls] == [
        2,
        2,
        1,
    ]
    assert [len(batch) for batch in embedding.batches] == [
        2,
        2,
        1,
    ]
    assert repository.delete_calls == []


@pytest.mark.asyncio
async def test_partial_failure_compensates_document_points() -> None:
    embedding = FakeEmbeddingProvider()
    repository = FakeVectorRepository(
        fail_on_upsert_call=2,
    )
    service = IndexingService(
        embedding_provider=embedding,
        vector_repository=repository,
        batch_size=2,
    )

    with pytest.raises(
        RuntimeError,
        match="simulated qdrant failure",
    ):
        await service.index_document(
            document=_document(),
            chunks=_chunks(5),
        )

    assert [len(batch) for batch in repository.upsert_calls] == [
        2,
        2,
    ]
    assert repository.delete_calls == [(12, 70)]


def test_rejects_non_positive_batch_size() -> None:
    with pytest.raises(
        ValueError,
        match="batch_size must be greater than zero",
    ):
        IndexingService(
            embedding_provider=FakeEmbeddingProvider(),
            vector_repository=FakeVectorRepository(),
            batch_size=0,
        )
