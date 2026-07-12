from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.router import (
    FileTypeConflictError,
    ParserRouter,
    UnsupportedFileTypeError,
)
from app.ingestion.service import IngestionService
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.document_status import DocumentStatus
from app.models.workspace import Workspace


def test_parser_router_selects_markdown_from_extension() -> None:
    selection = ParserRouter().select(
        filename="guide.md",
        content_type="application/octet-stream",
    )

    assert selection.file_type == "markdown"


def test_parser_router_rejects_conflicting_signals() -> None:
    with pytest.raises(FileTypeConflictError):
        ParserRouter().select(
            filename="guide.pdf",
            content_type="text/markdown",
        )


def test_parser_router_rejects_unsupported_file() -> None:
    with pytest.raises(UnsupportedFileTypeError):
        ParserRouter().select(
            filename="guide.docx",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        )


@pytest.mark.asyncio
async def test_ingestion_service_persists_completed_document() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.add = Mock()

    workspace = Workspace(id=1, name="Test")
    session.get.return_value = workspace

    async def refresh(instance: object) -> None:
        if isinstance(instance, Document):
            instance.id = 100

    session.refresh.side_effect = refresh

    service = IngestionService(session)

    result = await service.ingest(
        workspace_id=1,
        filename="guide.md",
        content_type="text/markdown",
        file_bytes=(
            b"# Authentication\n\n"
            b"Use an API key.\n"
        ),
    )

    assert result.document_id == 100
    assert result.status == DocumentStatus.COMPLETED.value
    assert result.file_type == "markdown"
    assert result.chunk_count == 1
    assert len(result.checksum) == 64
    assert session.commit.await_count == 2

    persisted_objects = [
        call.args[0]
        for call in session.add.call_args_list
    ]

    documents = [
        item
        for item in persisted_objects
        if isinstance(item, Document)
    ]
    chunks = [
        item
        for item in persisted_objects
        if isinstance(item, Chunk)
    ]

    assert len(documents) == 1
    assert len(chunks) == 1
    assert documents[0].status == DocumentStatus.COMPLETED.value
    assert all(chunk.document_id == 100 for chunk in chunks)
    assert all(chunk.metadata_json is not None for chunk in chunks)


@pytest.mark.asyncio
async def test_ingestion_failure_keeps_failed_document() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.add = Mock()

    workspace = Workspace(id=1, name="Test")
    captured_document: Document | None = None

    def add(instance: object) -> None:
        nonlocal captured_document
        if isinstance(instance, Document):
            captured_document = instance

    session.add.side_effect = add

    async def get(model: type, object_id: int) -> object | None:
        if model is Workspace:
            return workspace
        if model is Document:
            return captured_document
        return None

    async def refresh(instance: object) -> None:
        if isinstance(instance, Document):
            instance.id = 101

    session.get.side_effect = get
    session.refresh.side_effect = refresh

    service = IngestionService(session)

    with pytest.raises(
        ValueError,
        match="Markdown file must be valid UTF-8",
    ):
        await service.ingest(
            workspace_id=1,
            filename="broken.md",
            content_type="text/markdown",
            file_bytes=b"\xff\xfe\xfd",
        )

    assert captured_document is not None
    assert captured_document.status == DocumentStatus.FAILED.value
    assert captured_document.error_message
    assert session.rollback.await_count == 1
    assert session.commit.await_count == 2
