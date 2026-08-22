import asyncio
from dataclasses import dataclass
from typing import Sequence

from app.models.chunk import Chunk
from app.models.document import Document
from app.retrieval.dto import ChunkVectorPayload, VectorPoint
from app.retrieval.embedding import EmbeddingProvider
from app.retrieval.repository import VectorRepository


DEFAULT_INDEXING_BATCH_SIZE = 128


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
        batch_size: int = DEFAULT_INDEXING_BATCH_SIZE,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")

        self._embedding_provider = embedding_provider
        self._vector_repository = vector_repository
        self._batch_size = batch_size

    async def index_document(
        self,
        *,
        document: Document,
        chunks: Sequence[Chunk],
    ) -> IndexingResult:
        """Embed and index persisted chunks in bounded, compensatable batches."""

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

        await self._vector_repository.ensure_collection()

        indexed_chunk_count = 0

        try:
            for start in range(
                0,
                len(chunk_list),
                self._batch_size,
            ):
                batch = chunk_list[
                    start : start + self._batch_size
                ]

                vectors = await asyncio.to_thread(
                    self._embedding_provider.embed_documents,
                    [chunk.text for chunk in batch],
                )

                if len(vectors) != len(batch):
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
                        batch,
                        vectors,
                        strict=True,
                    )
                ]

                await self._vector_repository.upsert_points(
                    points
                )
                indexed_chunk_count += len(points)

        except Exception as indexing_exc:
            # A multi-batch write can partially succeed. PostgreSQL remains the
            # source of truth, so compensate by deleting every vector point for
            # this document before propagating the original indexing failure.
            try:
                await self._vector_repository.delete_document_points(
                    workspace_id=document.workspace_id,
                    document_id=document.id,
                )
            except Exception as cleanup_exc:
                indexing_exc.add_note(
                    "Qdrant compensation cleanup also failed: "
                    f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                )
            raise

        return IndexingResult(
            document_id=document.id,
            indexed_chunk_count=indexed_chunk_count,
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
