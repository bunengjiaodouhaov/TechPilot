from collections.abc import AsyncIterator
from functools import lru_cache

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.answering.answer_service import AnswerService
from app.answering.chunk_repository import ChunkRepository
from app.answering.context_builder import ContextBuilder
from app.answering.context_enricher import ContextEnricher
from app.answering.deepseek_evidence_verifier import DeepSeekEvidenceVerifierProvider
from app.answering.deepseek_llm import DeepSeekLLMProvider
from app.answering.evidence_verifier import EvidenceVerifierProvider
from app.answering.llm import LLMProvider
from app.answering.provider_retry import (
    RetryingEvidenceVerifierProvider,
    RetryingLLMProvider,
)
from app.answering.workspace_repository import WorkspaceRepository
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.documents.service import DocumentService
from app.ingestion.service import IngestionService
from app.jd.deepseek_extractor import DeepSeekJDExtractor
from app.retrieval.embedding import (
    EmbeddingProvider,
    SentenceTransformerEmbeddingProvider,
)
from app.retrieval.answer_retrieval_adapter import AnswerRetrievalAdapter
from app.retrieval.bm25_repository import BM25ChunkRepository
from app.retrieval.bm25_retrieval_service import BM25RetrievalService
from app.retrieval.dense_retrieval_service import DenseRetrievalService
from app.retrieval.hybrid_retrieval_service import HybridRetrievalService
from app.retrieval.indexing_service import IndexingService
from app.retrieval.qdrant_repository import QdrantRepository
from app.retrieval.reranker import (
    CrossEncoderRerankerProvider,
    RerankerProvider,
)
from app.retrieval.reranking_service import RerankingService
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


def get_document_service(
    session: AsyncSession = Depends(get_db_session),
) -> DocumentService:
    """Build the document lifecycle service for one HTTP request."""
    return DocumentService(
        session=session,
        vector_repository=get_vector_repository(),
    )


@lru_cache
def get_dense_retrieval_service() -> DenseRetrievalService:
    """Build and reuse the dense retrieval service."""
    return DenseRetrievalService(
        embedding_provider=get_embedding_provider(),
        vector_repository=get_vector_repository(),
    )


@lru_cache
def get_reranker_provider() -> RerankerProvider:
    # Build and reuse the configured CrossEncoder reranker.
    return CrossEncoderRerankerProvider(
        model_name=settings.reranker_model,
        batch_size=settings.reranker_batch_size,
        max_length=settings.reranker_max_length,
    )


def get_answer_retrieval_service(
    *,
    session: AsyncSession,
) -> AnswerRetrievalAdapter:
    # Build the measured Hybrid + CrossEncoder retrieval path for answering.
    bm25 = BM25RetrievalService(
        chunk_repository=BM25ChunkRepository(session=session),
        k1=settings.bm25_k1,
        b=settings.bm25_b,
    )
    hybrid = HybridRetrievalService(
        dense_retrieval_service=get_dense_retrieval_service(),
        bm25_retrieval_service=bm25,
        rrf_k=settings.answer_rrf_k,
    )
    reranking = RerankingService(
        hybrid_retrieval_service=hybrid,
        chunk_repository=ChunkRepository(session=session),
        reranker_provider=get_reranker_provider(),
    )
    return AnswerRetrievalAdapter(
        reranking_service=reranking,
        candidate_limit=settings.answer_retrieval_candidate_limit,
        rerank_depth=settings.answer_rerank_depth,
    )


@lru_cache
def get_llm_provider() -> LLMProvider:
    """Build and reuse the configured answer-generation provider."""
    return RetryingLLMProvider(
        provider=DeepSeekLLMProvider(
            api_key=settings.deepseek_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        ),
        max_attempts=3,
        base_delay_seconds=0.5,
    )


@lru_cache
def get_evidence_verifier_provider() -> EvidenceVerifierProvider:
    """Build and reuse the configured evidence-verification provider."""
    return RetryingEvidenceVerifierProvider(
        provider=DeepSeekEvidenceVerifierProvider(
            api_key=settings.deepseek_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        ),
        max_attempts=3,
        base_delay_seconds=0.5,
    )


def get_answer_service(
    session: AsyncSession = Depends(get_db_session),
) -> AnswerService:
    """Build the answering service with request-scoped database access."""
    return AnswerService(
        retrieval_service=get_answer_retrieval_service(session=session),
        chunk_repository=ChunkRepository(session=session),
        context_enricher=ContextEnricher(),
        context_builder=ContextBuilder(
            max_characters=settings.answer_context_max_characters,
        ),
        evidence_verifier=get_evidence_verifier_provider(),
        llm_provider=get_llm_provider(),
        workspace_repository=WorkspaceRepository(session=session),
        release_read_transaction=session.commit,
        recovery_enabled=settings.answer_recovery_enabled,
        recovery_anchor_limit=settings.answer_recovery_anchor_limit,
        recovery_parent_group_limit=settings.answer_recovery_parent_group_limit,
        recovery_max_additions=settings.answer_recovery_max_additions,
    )


@lru_cache
def get_jd_extractor() -> DeepSeekJDExtractor:
    """Build and reuse the configured JD structured-output provider."""
    return DeepSeekJDExtractor(
        api_key=settings.deepseek_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
