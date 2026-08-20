from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.api.schemas.workspace import WorkspaceCreateRequest, WorkspaceResponse
from app.workspaces.service import (
    WorkspaceNotEmptyError,
    WorkspaceNotFoundError,
    WorkspaceService,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def get_workspace_service(
    session: AsyncSession = Depends(get_db_session),
) -> WorkspaceService:
    return WorkspaceService(session=session)


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    service: WorkspaceService = Depends(get_workspace_service),
) -> list[WorkspaceResponse]:
    workspaces = await service.list_workspaces()
    return [WorkspaceResponse.model_validate(workspace) for workspace in workspaces]


@router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace(
    request: WorkspaceCreateRequest,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceResponse:
    workspace = await service.create_workspace(name=request.name)
    return WorkspaceResponse.model_validate(workspace)


@router.delete(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace(
    workspace_id: Annotated[int, Path(gt=0)],
    service: WorkspaceService = Depends(get_workspace_service),
) -> Response:
    try:
        await service.delete_workspace(workspace_id=workspace_id)
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkspaceNotEmptyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)
