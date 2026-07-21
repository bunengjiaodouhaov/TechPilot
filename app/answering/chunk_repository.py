from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.answering.dto import StoredChunk
from app.models.chunk import Chunk
from app.models.document import Document


class ChunkRepository:
    """Load authoritative chunk text and metadata from PostgreSQL."""

    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def get_by_ids(
        self,
        *,
        chunk_ids: Sequence[int],
        workspace_id: int,
    ) -> dict[int, StoredChunk]:
        """Return chunks belonging to one workspace, keyed by database ID."""

        if workspace_id <= 0:
            raise ValueError("workspace_id must be greater than zero")

        normalized_ids = list(dict.fromkeys(chunk_ids))

        if any(chunk_id <= 0 for chunk_id in normalized_ids):
            raise ValueError("chunk_ids must contain only positive integers")

        if not normalized_ids:
            return {}

        statement = (
            select(Chunk, Document)
            .join(
                Document,
                Chunk.document_id == Document.id,
            )
            .where(
                Chunk.id.in_(normalized_ids),
                Document.workspace_id == workspace_id,
            )
        )

        result = await self._session.execute(statement)

        rows = result.all()

        return {
            chunk.id: StoredChunk(
                chunk_db_id=chunk.id,
                chunk_id=chunk.chunk_id,
                document_id=document.id,
                document_name=document.name,
                source_type=document.file_type,
                chunk_index=chunk.chunk_index,
                section=chunk.section,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                text=chunk.text,
            )
            for chunk, document in rows
        }
