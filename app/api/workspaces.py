from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.api.schemas.workspace import WorkspaceCreateRequest, WorkspaceResponse
from app.auth.authorization import WorkspaceAccessError, WorkspaceAuthorizer, WorkspaceRoleError
from app.auth.dependencies import AuthPrincipal, get_current_user, get_workspace_authorizer
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


def _raise_access_error(exc: Exception) -> None:
    if isinstance(exc, WorkspaceAccessError):
        raise HTTPException(status_code=404, detail="workspace not found") from exc
    if isinstance(exc, WorkspaceRoleError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    raise exc


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    principal: Annotated[AuthPrincipal, Depends(get_current_user)],
    service: WorkspaceService = Depends(get_workspace_service),
) -> list[WorkspaceResponse]:
    workspaces = await service.list_workspaces(user_id=principal.id)
    return [WorkspaceResponse.model_validate(workspace) for workspace in workspaces]


@router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace(
    request: WorkspaceCreateRequest,
    principal: Annotated[AuthPrincipal, Depends(get_current_user)],
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceResponse:
    workspace = await service.create_workspace(
        name=request.name,
        owner_user_id=principal.id,
    )
    return WorkspaceResponse.model_validate(workspace)


@router.delete(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace(
    workspace_id: Annotated[int, Path(gt=0)],
    principal: Annotated[AuthPrincipal, Depends(get_current_user)],
    authorizer: Annotated[WorkspaceAuthorizer, Depends(get_workspace_authorizer)],
    service: WorkspaceService = Depends(get_workspace_service),
) -> Response:
    try:
        await authorizer.require_access(
            user_id=principal.id,
            workspace_id=workspace_id,
            owner_required=True,
        )
        await service.delete_workspace(workspace_id=workspace_id)
    except (WorkspaceAccessError, WorkspaceRoleError) as exc:
        _raise_access_error(exc)
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkspaceNotEmptyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)
