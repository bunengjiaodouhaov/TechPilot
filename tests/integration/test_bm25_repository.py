from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.db.session import AsyncSessionLocal, engine
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.document_status import DocumentStatus
from app.models.workspace import Workspace
from app.retrieval.bm25_repository import BM25ChunkRepository


@pytest_asyncio.fixture(autouse=True)
async def dispose_engine_after_test():
    """Prevent pooled asyncpg connections leaking across pytest event loops."""
    yield
    await engine.dispose()


async def add_document(
    *,
    session,
    workspace_id: int,
    name: str,
    status: str,
    deleted: bool = False,
) -> Document:
    document = Document(
        workspace_id=workspace_id,
        name=name,
        file_size_bytes=100,
        content_type="text/markdown",
        file_type="markdown",
        checksum=uuid4().hex,
        status=status,
        error_message=None,
        deleted_at=(
            datetime.now(timezone.utc)
            if deleted
            else None
        ),
    )
    session.add(document)
    await session.flush()

    session.add(
        Chunk(
            document_id=document.id,
            chunk_id=f"chunk-{uuid4().hex}",
            chunk_index=0,
            text=f"BM25 marker {name}",
            page_start=None,
            page_end=None,
            section="BM25",
            char_count=len(f"BM25 marker {name}"),
            metadata_json={},
        )
    )

    return document


@pytest.mark.asyncio
async def test_repository_returns_only_searchable_workspace_chunks() -> None:
    workspace_a = Workspace(name=f"bm25-a-{uuid4().hex}")
    workspace_b = Workspace(name=f"bm25-b-{uuid4().hex}")

    async with AsyncSessionLocal() as session:
        session.add_all([workspace_a, workspace_b])
        await session.commit()
        await session.refresh(workspace_a)
        await session.refresh(workspace_b)

        await add_document(
            session=session,
            workspace_id=workspace_a.id,
            name="completed.md",
            status=DocumentStatus.COMPLETED.value,
        )
        await add_document(
            session=session,
            workspace_id=workspace_a.id,
            name="partial.md",
            status=DocumentStatus.PARTIAL.value,
        )
        await add_document(
            session=session,
            workspace_id=workspace_a.id,
            name="failed.md",
            status=DocumentStatus.FAILED.value,
        )
        await add_document(
            session=session,
            workspace_id=workspace_a.id,
            name="deleted.md",
            status=DocumentStatus.COMPLETED.value,
            deleted=True,
        )
        await add_document(
            session=session,
            workspace_id=workspace_b.id,
            name="other-workspace.md",
            status=DocumentStatus.COMPLETED.value,
        )

        await session.commit()

        repository = BM25ChunkRepository(session=session)

        chunks = await repository.list_searchable(
            workspace_id=workspace_a.id,
        )

        assert {
            chunk.document_name
            for chunk in chunks
        } == {
            "completed.md",
            "partial.md",
        }

        assert all(
            chunk.workspace_id == workspace_a.id
            for chunk in chunks
        )

        await session.execute(
            delete(Workspace).where(
                Workspace.id.in_(
                    [workspace_a.id, workspace_b.id]
                )
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_repository_rejects_invalid_workspace_id() -> None:
    async with AsyncSessionLocal() as session:
        repository = BM25ChunkRepository(session=session)

        with pytest.raises(
            ValueError,
            match="workspace_id",
        ):
            await repository.list_searchable(
                workspace_id=0,
            )
