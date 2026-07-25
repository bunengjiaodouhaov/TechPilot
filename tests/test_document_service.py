from unittest.mock import AsyncMock, MagicMock

import pytest

from app.documents.service import (
    DocumentNotFoundError,
    DocumentService,
)
from app.models.document import Document


def build_session(document: Document | None) -> AsyncMock:
    session = AsyncMock()

    result = MagicMock()
    result.scalar_one_or_none.return_value = document
    session.execute.return_value = result

    return session


@pytest.mark.asyncio
async def test_delete_document_soft_deletes_and_removes_vectors() -> None:
    document = MagicMock(spec=Document)
    document.deleted_at = None

    session = build_session(document)
    vector_repository = AsyncMock()

    service = DocumentService(
        session=session,
        vector_repository=vector_repository,
    )

    await service.delete_document(
        workspace_id=10,
        document_id=20,
    )

    assert document.deleted_at is not None
    session.commit.assert_awaited_once()
    vector_repository.delete_document_points.assert_awaited_once_with(
        workspace_id=10,
        document_id=20,
    )


@pytest.mark.asyncio
async def test_delete_document_raises_when_document_not_found() -> None:
    session = build_session(None)
    vector_repository = AsyncMock()

    service = DocumentService(
        session=session,
        vector_repository=vector_repository,
    )

    with pytest.raises(
        DocumentNotFoundError,
        match="Document 20 does not exist in workspace 10",
    ):
        await service.delete_document(
            workspace_id=10,
            document_id=20,
        )

    session.commit.assert_not_awaited()
    vector_repository.delete_document_points.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_document_does_not_cleanup_vectors_when_commit_fails(
) -> None:
    document = MagicMock(spec=Document)
    document.deleted_at = None

    session = build_session(document)
    session.commit.side_effect = RuntimeError("database unavailable")

    vector_repository = AsyncMock()

    service = DocumentService(
        session=session,
        vector_repository=vector_repository,
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.delete_document(
            workspace_id=10,
            document_id=20,
        )

    vector_repository.delete_document_points.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_document_keeps_soft_delete_when_vector_cleanup_fails(
) -> None:
    document = MagicMock(spec=Document)
    document.deleted_at = None

    session = build_session(document)

    vector_repository = AsyncMock()
    vector_repository.delete_document_points.side_effect = RuntimeError(
        "qdrant unavailable"
    )

    service = DocumentService(
        session=session,
        vector_repository=vector_repository,
    )

    await service.delete_document(
        workspace_id=10,
        document_id=20,
    )

    assert document.deleted_at is not None
    session.commit.assert_awaited_once()
    vector_repository.delete_document_points.assert_awaited_once_with(
        workspace_id=10,
        document_id=20,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("workspace_id", "document_id", "message"),
    [
        (0, 20, "workspace_id must be greater than zero"),
        (10, 0, "document_id must be greater than zero"),
    ],
)
async def test_delete_document_rejects_invalid_ids(
    workspace_id: int,
    document_id: int,
    message: str,
) -> None:
    session = AsyncMock()
    vector_repository = AsyncMock()

    service = DocumentService(
        session=session,
        vector_repository=vector_repository,
    )

    with pytest.raises(ValueError, match=message):
        await service.delete_document(
            workspace_id=workspace_id,
            document_id=document_id,
        )

    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()
