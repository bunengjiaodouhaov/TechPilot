from collections.abc import AsyncIterator
from functools import lru_cache

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.answering.answer_service import AnswerService
from app.answering.chunk_repository import ChunkRepository
from app.answering.context_builder import ContextBuilder
from app.answering.context_enricher import ContextEnricher
from app.answering.deepseek_llm import DeepSeekLLMProvider
from app.answering.llm import LLMProvider
from app.answering.workspace_repository import WorkspaceRepository
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.ingestion.service import IngestionService
from app.retrieval.embedding import (
    EmbeddingProvider,
    SentenceTransformerEmbeddingProvider,
)
from app.retrieval.dense_retrieval_service import DenseRetrievalService
from app.retrieval.indexing_service import IndexingService
from app.retrieval.qdrant_repository import QdrantRepository
from app.retrieval.repository import VectorRepository


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Provide one database session for one HTTP request."""
    async with AsyncSessionLocal() as session:
        yield session


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    """Build and reuse the configured embedding provider."""
    return SentenceTransformerEmbeddingProvider(
        model_name=settings.embedding_model,
        dimension=settings.embedding_dimension,
        batch_size=settings.embedding_batch_size,
    )


@lru_cache
def get_vector_repository() -> VectorRepository:
    """Build and reuse the configured vector repository."""
    return QdrantRepository(
        qdrant_url=settings.qdrant_url,
        collection_name=settings.qdrant_collection_name,
        dimension=settings.embedding_dimension,
    )


@lru_cache
def get_indexing_service() -> IndexingService:
    """Build and reuse the document indexing service."""
    return IndexingService(
        embedding_provider=get_embedding_provider(),
        vector_repository=get_vector_repository(),
    )


def get_ingestion_service(
    session: AsyncSession = Depends(get_db_session),
) -> IngestionService:
    """Build the ingestion service with request-scoped database access."""
    return IngestionService(
        session=session,
        indexing_service=get_indexing_service(),
    )

@lru_cache
def get_dense_retrieval_service() -> DenseRetrievalService:
    """Build and reuse the dense retrieval service."""
    return DenseRetrievalService(
        embedding_provider=get_embedding_provider(),
        vector_repository=get_vector_repository(),
    )


@lru_cache
def get_llm_provider() -> LLMProvider:
    """Build and reuse the configured LLM provider."""
    return DeepSeekLLMProvider(
        api_key=settings.deepseek_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def get_answer_service(
    session: AsyncSession = Depends(get_db_session),
) -> AnswerService:
    """Build the answering service with request-scoped database access."""
    return AnswerService(
        retrieval_service=get_dense_retrieval_service(),
        chunk_repository=ChunkRepository(session=session),
        context_enricher=ContextEnricher(),
        context_builder=ContextBuilder(
            max_characters=settings.answer_context_max_characters,
        ),
        llm_provider=get_llm_provider(),
        workspace_repository=WorkspaceRepository(session=session),
    )

