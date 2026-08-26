from collections.abc import Sequence

from sqlalchemy import and_, or_, select
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
                Document.deleted_at.is_(None),
            )
        )

        result = await self._session.execute(statement)

        rows = result.all()

        return {
            chunk.id: self._to_stored_chunk(chunk=chunk, document=document)
            for chunk, document in rows
        }

    async def get_by_parent_sections(
        self,
        *,
        parent_sections: Sequence[tuple[int, str]],
        workspace_id: int,
    ) -> list[StoredChunk]:
        """Load bounded-recovery siblings under selected document sections.

        Each tuple is ``(document_id, parent_section)``. The query remains
        workspace scoped and excludes soft-deleted documents. A parent matches
        both a chunk whose section is exactly the parent and descendants using
        the ``parent > child`` hierarchy emitted by structured parsers.
        """

        if workspace_id <= 0:
            raise ValueError("workspace_id must be greater than zero")

        normalized: list[tuple[int, str]] = []
        seen: set[tuple[int, str]] = set()
        for document_id, parent in parent_sections:
            if document_id <= 0:
                raise ValueError("document_id must be greater than zero")
            section = parent.strip()
            if not section:
                raise ValueError("parent section must not be empty")
            key = (document_id, section)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(key)

        if not normalized:
            return []

        section_conditions = [
            and_(
                Document.id == document_id,
                or_(
                    Chunk.section == parent,
                    Chunk.section.startswith(parent + " > "),
                ),
            )
            for document_id, parent in normalized
        ]

        statement = (
            select(Chunk, Document)
            .join(Document, Chunk.document_id == Document.id)
            .where(
                Document.workspace_id == workspace_id,
                Document.deleted_at.is_(None),
                or_(*section_conditions),
            )
            .order_by(Document.id.asc(), Chunk.chunk_index.asc())
        )

        result = await self._session.execute(statement)
        return [
            self._to_stored_chunk(chunk=chunk, document=document)
            for chunk, document in result.all()
        ]

    @staticmethod
    def _to_stored_chunk(*, chunk: Chunk, document: Document) -> StoredChunk:
        return StoredChunk(
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
