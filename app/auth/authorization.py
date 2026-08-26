from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace_member import WorkspaceMember, WorkspaceRole


class WorkspaceAccessError(LookupError):
    """Raised when a principal has no membership in a workspace."""


class WorkspaceRoleError(PermissionError):
    """Raised when membership exists but its role is insufficient."""


class WorkspaceAuthorizer:
    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def require_access(
        self,
        *,
        user_id: int,
        workspace_id: int,
        owner_required: bool = False,
    ) -> WorkspaceMember:
        membership = await self._session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.workspace_id == workspace_id,
            )
        )
        if membership is None:
            raise WorkspaceAccessError("workspace not found")
        if owner_required and membership.role != WorkspaceRole.OWNER.value:
            raise WorkspaceRoleError("workspace owner permission required")
        return membership
