from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.chunker import StructureAwareChunker
from app.ingestion.router import ParserRouter
from app.ingestion.schemas import ParseInput
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.document_status import DocumentStatus
from app.models.workspace import Workspace


class WorkspaceNotFoundError(ValueError):
    """Raised when the target workspace does not exist."""


class EmptyDocumentError(ValueError):
    """Raised when parsing produces no usable chunks."""


@dataclass(frozen=True, slots=True)
class IngestionResult:
    document_id: int
    status: str
    file_type: str
    chunk_count: int
    checksum: str


class IngestionService:
    """Run one uploaded file through the ingestion pipeline."""

    def __init__(
        self,
        session: AsyncSession,
        router: ParserRouter | None = None,
        chunker: StructureAwareChunker | None = None,
    ) -> None:
        self._session = session
        self._router = router or ParserRouter()
        self._chunker = chunker or StructureAwareChunker()

    async def ingest(
        self,
        *,
        workspace_id: int,
        filename: str,
        content_type: str,
        file_bytes: bytes,
    ) -> IngestionResult:
        if not filename.strip():
            raise ValueError("filename must not be empty.")

        if not file_bytes:
            raise ValueError("Uploaded file must not be empty.")

        selection = self._router.select(
            filename=filename,
            content_type=content_type,
        )

        workspace = await self._session.get(
            Workspace,
            workspace_id,
        )
        if workspace is None:
            raise WorkspaceNotFoundError(
                f"Workspace {workspace_id} does not exist."
            )

        checksum = sha256(file_bytes).hexdigest()

        document = Document(
            workspace_id=workspace_id,
            name=filename,
            file_size_bytes=len(file_bytes),
            content_type=content_type,
            file_type=selection.file_type,
            checksum=checksum,
            status=DocumentStatus.PENDING.value,
            error_message=None,
        )

        # Transaction boundary 1:
        # Persist the ingestion record before parsing starts.
        self._session.add(document)
        await self._session.commit()
        await self._session.refresh(document)

        try:
            parse_input = ParseInput(
                filename=filename,
                content_type=content_type,
                file_size=len(file_bytes),
                file_bytes=file_bytes,
            )

            parsed_document = selection.parser.parse(parse_input)

            if parsed_document.file_type != selection.file_type:
                raise ValueError(
                    "Parser returned a file type inconsistent with "
                    "the selected parser."
                )

            chunk_data = self._chunker.chunk(parsed_document)

            if not chunk_data:
                raise EmptyDocumentError(
                    "Parsing produced no usable chunks."
                )

            for item in chunk_data:
                self._session.add(
                    Chunk(
                        document_id=document.id,
                        chunk_id=item.chunk_id,
                        chunk_index=item.chunk_index,
                        text=item.text,
                        page_start=item.page_start,
                        page_end=item.page_end,
                        section=item.section,
                        char_count=item.char_count,
                        metadata_json=item.metadata,
                    )
                )

            failed_pages = (
                parsed_document.metadata.get("failed_pages") or []
            )

            if failed_pages:
                document.status = DocumentStatus.PARTIAL.value
                document.error_message = (
                    "Text extraction failed for pages: "
                    + ", ".join(str(page) for page in failed_pages)
                )
            else:
                document.status = DocumentStatus.COMPLETED.value
                document.error_message = None

            # Transaction boundary 2:
            # All chunks and the final Document state commit together.
            await self._session.commit()

            return IngestionResult(
                document_id=document.id,
                status=document.status,
                file_type=document.file_type,
                chunk_count=len(chunk_data),
                checksum=document.checksum,
            )

        except Exception as exc:
            # Remove any uncommitted chunks from the failed attempt.
            await self._session.rollback()

            failed_document = await self._session.get(
                Document,
                document.id,
            )

            if failed_document is not None:
                failed_document.status = DocumentStatus.FAILED.value
                failed_document.error_message = self._error_message(exc)
                await self._session.commit()

            raise

    @staticmethod
    def _error_message(exc: Exception) -> str:
        message = str(exc).strip()
        result = message or exc.__class__.__name__
        return result[:2000]
