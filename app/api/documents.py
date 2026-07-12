from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel

from app.api.dependencies import get_ingestion_service
from app.ingestion.router import (
    FileTypeConflictError,
    UnsupportedFileTypeError,
)
from app.ingestion.service import (
    EmptyDocumentError,
    IngestionService,
    WorkspaceNotFoundError,
)

router = APIRouter(
    prefix="/documents",
    tags=["documents"],
)


class DocumentUploadResponse(BaseModel):
    """Public response returned after a successful ingestion."""

    document_id: int
    filename: str
    status: str
    file_type: str
    chunk_count: int
    checksum: str


@router.post(
    "/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    workspace_id: Annotated[int, Form(gt=0)],
    file: Annotated[UploadFile, File()],
    service: Annotated[
        IngestionService,
        Depends(get_ingestion_service),
    ],
) -> DocumentUploadResponse:
    """Upload, parse, chunk, and persist one PDF or Markdown file."""
    filename = file.filename or ""
    content_type = (
        file.content_type
        or "application/octet-stream"
    )

    try:
        file_bytes = await file.read()

        result = await service.ingest(
            workspace_id=workspace_id,
            filename=filename,
            content_type=content_type,
            file_bytes=file_bytes,
        )
    except WorkspaceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except (
        UnsupportedFileTypeError,
        FileTypeConflictError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(exc),
        ) from exc
    except EmptyDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    finally:
        await file.close()

    return DocumentUploadResponse(
        document_id=result.document_id,
        filename=filename,
        status=result.status,
        file_type=result.file_type,
        chunk_count=result.chunk_count,
        checksum=result.checksum,
    )
