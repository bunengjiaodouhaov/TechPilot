from collections.abc import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.ingestion.service import IngestionService


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Provide one database session for one HTTP request."""
    async with AsyncSessionLocal() as session:
        yield session


def get_ingestion_service(
    session: AsyncSession = Depends(get_db_session),
) -> IngestionService:
    """Build the ingestion service with the request database session."""
    return IngestionService(session)
