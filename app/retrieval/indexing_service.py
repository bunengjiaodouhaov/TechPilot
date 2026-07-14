import asyncio
from dataclasses import dataclass
from typing import Sequence

from app.models.chunk import Chunk
from app.models.document import Document
from app.retrieval.dto import ChunkVectorPayload, VectorPoint
from app.retrieval.embedding import EmbeddingProvider
from app.retrieval.repository import VectorRepository


@dataclass(frozen=True)
class IndexingResult:
    """Summary returned after one document is indexed."""

    document_id: int
    indexed_chunk_count: int


class IndexingService:
    """Build vector indexes for persisted document chunks."""

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        vector_repository: VectorRepository,
    ) -> None:
        self._embedding_provider = embedding_provider
        self._vector_repository = vector_repository

    async def index_document(
        self,
        *,
        document: Document,
        chunks: Sequence[Chunk],
    ) -> IndexingResult:
        """Embed and index all persisted chunks belonging to one document."""

        if document.id is None:
            raise ValueError(
                "document must be persisted before indexing"
            )

        if document.workspace_id is None:
            raise ValueError(
                "document workspace_id must not be empty"
            )

        chunk_list = list(chunks)

        if not chunk_list:
            raise ValueError(
                "document must contain at least one chunk"
            )

        self._validate_chunks(
            document_id=document.id,
            chunks=chunk_list,
        )

        texts = [chunk.text for chunk in chunk_list]

        vectors = await asyncio.to_thread(
            self._embedding_provider.embed_documents,
            texts,
        )

        if len(vectors) != len(chunk_list):
            raise ValueError(
                "embedding count does not match chunk count"
            )

        points = [
            self._build_point(
                document=document,
                chunk=chunk,
                vector=vector,
            )
            for chunk, vector in zip(
                chunk_list,
                vectors,
                strict=True,
            )
        ]

        await self._vector_repository.ensure_collection()
        await self._vector_repository.upsert_points(points)

        return IndexingResult(
            document_id=document.id,
            indexed_chunk_count=len(points),
        )

    @staticmethod
    def _validate_chunks(
        *,
        document_id: int,
        chunks: list[Chunk],
    ) -> None:
        """Validate that chunks are persisted and belong to the document."""

        for chunk in chunks:
            if chunk.id is None:
                raise ValueError(
                    "all chunks must be persisted before indexing"
                )

            if chunk.document_id != document_id:
                raise ValueError(
                    "all chunks must belong to the target document"
                )

            if not chunk.text.strip():
                raise ValueError(
                    "chunk text must not be empty"
                )

    @staticmethod
    def _build_point(
        *,
        document: Document,
        chunk: Chunk,
        vector: list[float],
    ) -> VectorPoint:
        """Convert one persisted chunk and vector into a VectorPoint."""

        if chunk.id is None:
            raise ValueError(
                "chunk must be persisted before building a vector point"
            )

        return VectorPoint(
            point_id=chunk.id,
            vector=vector,
            payload=ChunkVectorPayload(
                workspace_id=document.workspace_id,
                document_id=document.id,
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.chunk_index,
                section=chunk.section,
                document_name=document.name,
                source_type=document.file_type,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
            ),
        )
