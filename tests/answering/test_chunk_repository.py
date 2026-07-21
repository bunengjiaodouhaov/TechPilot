from typing import Any

import pytest

from app.answering.chunk_repository import ChunkRepository


class FakeResult:
    def __init__(
        self,
        *,
        rows: list[tuple[Any, Any]],
    ) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, Any]]:
        return self._rows


class FakeSession:
    def __init__(
        self,
        *,
        rows: list[tuple[Any, Any]] | None = None,
    ) -> None:
        self.rows = rows or []
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> FakeResult:
        self.statements.append(statement)
        return FakeResult(rows=self.rows)


class FakeChunk:
    def __init__(
        self,
        *,
        chunk_db_id: int,
        document_id: int,
    ) -> None:
        self.id = chunk_db_id
        self.chunk_id = f"chunk-{chunk_db_id}"
        self.document_id = document_id
        self.chunk_index = 2
        self.section = "Transactions"
        self.page_start = 3
        self.page_end = 4
        self.text = "Authoritative PostgreSQL chunk text."


class FakeDocument:
    def __init__(
        self,
        *,
        document_id: int,
    ) -> None:
        self.id = document_id
        self.name = "postgresql.pdf"
        self.file_type = "pdf"


@pytest.mark.asyncio
async def test_get_by_ids_returns_chunks_keyed_by_database_id() -> None:
    chunk = FakeChunk(
        chunk_db_id=101,
        document_id=10,
    )
    document = FakeDocument(document_id=10)

    session = FakeSession(
        rows=[(chunk, document)],
    )

    repository = ChunkRepository(session=session)  # type: ignore[arg-type]

    result = await repository.get_by_ids(
        chunk_ids=[101],
        workspace_id=1,
    )

    assert list(result) == [101]

    stored_chunk = result[101]

    assert stored_chunk.chunk_db_id == 101
    assert stored_chunk.chunk_id == "chunk-101"
    assert stored_chunk.document_id == 10
    assert stored_chunk.document_name == "postgresql.pdf"
    assert stored_chunk.source_type == "pdf"
    assert stored_chunk.chunk_index == 2
    assert stored_chunk.section == "Transactions"
    assert stored_chunk.page_start == 3
    assert stored_chunk.page_end == 4
    assert stored_chunk.text == "Authoritative PostgreSQL chunk text."

    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_get_by_ids_uses_one_query_for_multiple_ids() -> None:
    session = FakeSession()

    repository = ChunkRepository(session=session)  # type: ignore[arg-type]

    await repository.get_by_ids(
        chunk_ids=[1, 8, 15, 29],
        workspace_id=1,
    )

    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_get_by_ids_deduplicates_input_ids() -> None:
    session = FakeSession()

    repository = ChunkRepository(session=session)  # type: ignore[arg-type]

    await repository.get_by_ids(
        chunk_ids=[1, 1, 8, 8],
        workspace_id=1,
    )

    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_get_by_ids_returns_empty_without_query() -> None:
    session = FakeSession()

    repository = ChunkRepository(session=session)  # type: ignore[arg-type]

    result = await repository.get_by_ids(
        chunk_ids=[],
        workspace_id=1,
    )

    assert result == {}
    assert session.statements == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "workspace_id",
    [0, -1],
)
async def test_get_by_ids_rejects_invalid_workspace_id(
    workspace_id: int,
) -> None:
    session = FakeSession()

    repository = ChunkRepository(session=session)  # type: ignore[arg-type]

    with pytest.raises(
        ValueError,
        match="workspace_id must be greater than zero",
    ):
        await repository.get_by_ids(
            chunk_ids=[1],
            workspace_id=workspace_id,
        )

    assert session.statements == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunk_ids",
    [
        [0],
        [-1],
        [1, 0],
    ],
)
async def test_get_by_ids_rejects_invalid_chunk_ids(
    chunk_ids: list[int],
) -> None:
    session = FakeSession()

    repository = ChunkRepository(session=session)  # type: ignore[arg-type]

    with pytest.raises(
        ValueError,
        match="chunk_ids must contain only positive integers",
    ):
        await repository.get_by_ids(
            chunk_ids=chunk_ids,
            workspace_id=1,
        )

    assert session.statements == []
