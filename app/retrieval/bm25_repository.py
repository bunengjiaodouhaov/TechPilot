from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document
from app.models.document_status import DocumentStatus
from app.retrieval.bm25_dto import BM25Chunk


class BM25ChunkRepositoryProtocol(Protocol):
    async def list_searchable(
        self,
        *,
        workspace_id: int,
    ) -> list[BM25Chunk]:
        """Return BM25-eligible chunks inside one workspace."""
        ...


class BM25ChunkRepository:
    """Load the legal BM25 corpus from PostgreSQL."""

    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def list_searchable(
        self,
        *,
        workspace_id: int,
    ) -> list[BM25Chunk]:
        if workspace_id <= 0:
            raise ValueError("workspace_id must be greater than zero")

        statement = (
            select(Chunk, Document)
            .join(
                Document,
                Chunk.document_id == Document.id,
            )
            .where(
                Document.workspace_id == workspace_id,
                Document.deleted_at.is_(None),
                Document.status.in_(
                    (
                        DocumentStatus.COMPLETED.value,
                        DocumentStatus.PARTIAL.value,
                    )
                ),
            )
            .order_by(Chunk.id)
        )

        result = await self._session.execute(statement)

        return [
            BM25Chunk(
                point_id=chunk.id,
                workspace_id=document.workspace_id,
                document_id=document.id,
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.chunk_index,
                section=chunk.section,
                document_name=document.name,
                source_type=document.file_type,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                text=chunk.text,
            )
            for chunk, document in result.all()
        ]
