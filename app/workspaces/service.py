from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember, WorkspaceRole


class WorkspaceNotFoundError(LookupError):
    """Raised when a workspace cannot be found."""


class WorkspaceNotEmptyError(RuntimeError):
    """Raised when deleting a workspace that still has active documents."""


class WorkspaceService:
    """Manage workspace lifecycle with explicit user ownership."""

    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def list_workspaces(self, *, user_id: int) -> list[Workspace]:
        if user_id <= 0:
            raise ValueError("user_id must be greater than zero")
        statement = (
            select(Workspace)
            .join(
                WorkspaceMember,
                WorkspaceMember.workspace_id == Workspace.id,
            )
            .where(WorkspaceMember.user_id == user_id)
            .order_by(Workspace.updated_at.desc(), Workspace.id.desc())
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def create_workspace(self, *, name: str, owner_user_id: int) -> Workspace:
        normalized = name.strip()
        if not normalized:
            raise ValueError("workspace name must not be empty")
        if len(normalized) > 255:
            raise ValueError("workspace name must be at most 255 characters")
        if owner_user_id <= 0:
            raise ValueError("owner_user_id must be greater than zero")

        workspace = Workspace(name=normalized)
        self._session.add(workspace)
        await self._session.flush()
        self._session.add(
            WorkspaceMember(
                user_id=owner_user_id,
                workspace_id=workspace.id,
                role=WorkspaceRole.OWNER.value,
            )
        )
        await self._session.commit()
        await self._session.refresh(workspace)
        return workspace

    async def delete_workspace(self, *, workspace_id: int) -> None:
        if workspace_id <= 0:
            raise ValueError("workspace_id must be greater than zero")

        workspace = await self._session.get(Workspace, workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(f"Workspace {workspace_id} does not exist.")

        active_documents = await self._session.scalar(
            select(func.count(Document.id)).where(
                Document.workspace_id == workspace_id,
                Document.deleted_at.is_(None),
            )
        )
        if int(active_documents or 0) > 0:
            raise WorkspaceNotEmptyError(
                "Workspace still contains active documents. Remove its sources before deleting the workspace."
            )

        await self._session.delete(workspace)
        await self._session.commit()
