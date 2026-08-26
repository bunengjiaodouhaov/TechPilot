from __future__ import annotations

import hashlib

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Path,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel

from app.api.dependencies import get_document_service, get_ingestion_service
from app.auth.authorization import WorkspaceAccessError, WorkspaceAuthorizer, WorkspaceRoleError
from app.auth.dependencies import (
    AuthPrincipal,
    get_current_user,
    get_idempotency_service,
    get_workspace_authorizer,
)
from app.auth.idempotency import IdempotencyConflictError, IdempotencyService
from app.documents.service import DocumentNotFoundError, DocumentService
from app.ingestion.router import FileTypeConflictError, UnsupportedFileTypeError
from app.ingestion.service import EmptyDocumentError, IngestionService, WorkspaceNotFoundError

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentUploadResponse(BaseModel):
    document_id: int
    filename: str
    status: str
    file_type: str
    chunk_count: int
    checksum: str


def _upload_hash(
    *,
    workspace_id: int,
    filename: str,
    content_type: str,
    file_bytes: bytes,
) -> str:
    digest = hashlib.sha256()
    digest.update(str(workspace_id).encode("ascii"))
    digest.update(b"\0")
    digest.update(filename.encode("utf-8"))
    digest.update(b"\0")
    digest.update(content_type.encode("utf-8"))
    digest.update(b"\0")
    digest.update(file_bytes)
    return digest.hexdigest()


def _workspace_access_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, WorkspaceAccessError):
        return HTTPException(status_code=404, detail="workspace not found")
    if isinstance(exc, WorkspaceRoleError):
        return HTTPException(status_code=403, detail=str(exc))
    raise exc


@router.post(
    "/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    workspace_id: Annotated[int, Form(gt=0)],
    file: Annotated[UploadFile, File()],
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
    principal: Annotated[AuthPrincipal, Depends(get_current_user)],
    authorizer: Annotated[WorkspaceAuthorizer, Depends(get_workspace_authorizer)],
    idempotency: Annotated[IdempotencyService, Depends(get_idempotency_service)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
) -> DocumentUploadResponse:
    filename = file.filename or ""
    content_type = file.content_type or "application/octet-stream"
    record_id: int | None = None

    try:
        await authorizer.require_access(
            user_id=principal.id,
            workspace_id=workspace_id,
        )
        file_bytes = await file.read()

        if idempotency_key is not None:
            decision = await idempotency.begin(
                user_id=principal.id,
                workspace_id=workspace_id,
                operation="document_upload",
                key=idempotency_key,
                request_hash=_upload_hash(
                    workspace_id=workspace_id,
                    filename=filename,
                    content_type=content_type,
                    file_bytes=file_bytes,
                ),
            )
            if decision.is_replay:
                assert decision.replay_response is not None
                return DocumentUploadResponse.model_validate(decision.replay_response)
            record_id = decision.record_id

        result = await service.ingest(
            workspace_id=workspace_id,
            filename=filename,
            content_type=content_type,
            file_bytes=file_bytes,
        )
        response = DocumentUploadResponse(
            document_id=result.document_id,
            filename=filename,
            status=result.status,
            file_type=result.file_type,
            chunk_count=result.chunk_count,
            checksum=result.checksum,
        )
        if record_id is not None:
            await idempotency.complete(
                record_id=record_id,
                status_code=status.HTTP_201_CREATED,
                response_json=response.model_dump(mode="json"),
            )
        return response

    except (WorkspaceAccessError, WorkspaceRoleError) as exc:
        if record_id is not None:
            await idempotency.fail(record_id=record_id)
        raise _workspace_access_http_error(exc) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkspaceNotFoundError as exc:
        if record_id is not None:
            await idempotency.fail(record_id=record_id)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (UnsupportedFileTypeError, FileTypeConflictError) as exc:
        if record_id is not None:
            await idempotency.fail(record_id=record_id)
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except EmptyDocumentError as exc:
        if record_id is not None:
            await idempotency.fail(record_id=record_id)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        if record_id is not None:
            await idempotency.fail(record_id=record_id)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        if record_id is not None:
            await idempotency.fail(record_id=record_id)
        raise
    finally:
        await file.close()


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: Annotated[int, Path(gt=0)],
    workspace_id: Annotated[int, Query(gt=0)],
    service: Annotated[DocumentService, Depends(get_document_service)],
    principal: Annotated[AuthPrincipal, Depends(get_current_user)],
    authorizer: Annotated[WorkspaceAuthorizer, Depends(get_workspace_authorizer)],
) -> Response:
    try:
        await authorizer.require_access(
            user_id=principal.id,
            workspace_id=workspace_id,
        )
        await service.delete_document(
            workspace_id=workspace_id,
            document_id=document_id,
        )
    except (WorkspaceAccessError, WorkspaceRoleError) as exc:
        raise _workspace_access_http_error(exc) from exc
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)
