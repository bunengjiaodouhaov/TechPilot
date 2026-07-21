from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace


class WorkspaceRepository:
    """Read workspace state from PostgreSQL."""

    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def exists(self, *, workspace_id: int) -> bool:
        """Return whether the workspace exists."""
        statement = (
            select(Workspace.id)
            .where(Workspace.id == workspace_id)
            .limit(1)
        )
        result = await self._session.scalar(statement)
        return result is not None
