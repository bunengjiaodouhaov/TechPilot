from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.auth.authorization import WorkspaceAccessError, WorkspaceAuthorizer, WorkspaceRoleError
from app.auth.dependencies import AuthPrincipal, get_current_user, get_workspace_authorizer
from app.models.chunk import Chunk
from app.models.document import Document

router = APIRouter(tags=["product-memory"])


class PersistentDocumentResponse(BaseModel):
    id: int
    workspace_id: int
    name: str
    file_type: str
    status: str
    checksum: str
    file_size_bytes: int
    chunk_count: int
    created_at: datetime


@router.get(
    "/workspaces/{workspace_id}/documents",
    response_model=list[PersistentDocumentResponse],
)
async def list_workspace_documents(
    workspace_id: Annotated[int, Path(gt=0)],
    session: AsyncSession = Depends(get_db_session),
    principal: AuthPrincipal = Depends(get_current_user),
    authorizer: WorkspaceAuthorizer = Depends(get_workspace_authorizer),
) -> list[PersistentDocumentResponse]:
    try:
        await authorizer.require_access(
            user_id=principal.id,
            workspace_id=workspace_id,
        )
    except WorkspaceAccessError as exc:
        raise HTTPException(status_code=404, detail="workspace not found") from exc
    except WorkspaceRoleError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    statement = (
        select(Document, func.count(Chunk.id).label("chunk_count"))
        .outerjoin(Chunk, Chunk.document_id == Document.id)
        .where(
            Document.workspace_id == workspace_id,
            Document.deleted_at.is_(None),
        )
        .group_by(Document.id)
        .order_by(Document.created_at.desc(), Document.id.desc())
    )
    rows = (await session.execute(statement)).all()
    return [
        PersistentDocumentResponse(
            id=document.id,
            workspace_id=document.workspace_id,
            name=document.name,
            file_type=document.file_type,
            status=document.status,
            checksum=document.checksum,
            file_size_bytes=document.file_size_bytes,
            chunk_count=int(chunk_count or 0),
            created_at=document.created_at,
        )
        for document, chunk_count in rows
    ]
