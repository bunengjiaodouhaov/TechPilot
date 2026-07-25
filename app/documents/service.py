from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.retrieval.repository import VectorRepository


logger = logging.getLogger(__name__)


class DocumentNotFoundError(LookupError):
    """Raised when an active document cannot be found in a workspace."""


class DocumentService:
    """Manage the lifecycle of persisted documents."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        vector_repository: VectorRepository,
    ) -> None:
        self._session = session
        self._vector_repository = vector_repository

    async def delete_document(
        self,
        *,
        workspace_id: int,
        document_id: int,
    ) -> None:
        """Soft-delete a document and best-effort remove its vectors."""

        if workspace_id <= 0:
            raise ValueError("workspace_id must be greater than zero")

        if document_id <= 0:
            raise ValueError("document_id must be greater than zero")

        statement = select(Document).where(
            Document.id == document_id,
            Document.workspace_id == workspace_id,
            Document.deleted_at.is_(None),
        )

        result = await self._session.execute(statement)
        document = result.scalar_one_or_none()

        if document is None:
            raise DocumentNotFoundError(
                f"Document {document_id} does not exist "
                f"in workspace {workspace_id}."
            )

        document.deleted_at = datetime.now(timezone.utc)

        # PostgreSQL is the source of truth. The document becomes
        # unavailable immediately after this commit succeeds.
        await self._session.commit()

        try:
            await self._vector_repository.delete_document_points(
                workspace_id=workspace_id,
                document_id=document_id,
            )
        except Exception:
            # Vector cleanup is eventually consistent. A failure here must
            # not restore a successfully soft-deleted PostgreSQL document.
            logger.exception(
                "Failed to delete vectors for document %s "
                "in workspace %s",
                document_id,
                workspace_id,
            )
