from collections.abc import Mapping, Sequence

from app.answering.dto import (
    RetrievedContext,
    RetrievedContextBatch,
    StoredChunk,
)
from app.retrieval.dto import VectorSearchHit


class ContextEnricher:
    """Combine vector-search hits with authoritative PostgreSQL chunks."""

    def enrich(
        self,
        *,
        hits: Sequence[VectorSearchHit],
        stored_chunks: Mapping[int, StoredChunk],
    ) -> RetrievedContextBatch:
        contexts: list[RetrievedContext] = []
        missing_chunk_db_ids: list[int] = []

        for rank, hit in enumerate(hits, start=1):
            stored_chunk = stored_chunks.get(hit.point_id)

            if stored_chunk is None:
                missing_chunk_db_ids.append(hit.point_id)
                continue

            contexts.append(
                RetrievedContext(
                    chunk_db_id=stored_chunk.chunk_db_id,
                    chunk_id=stored_chunk.chunk_id,
                    document_id=stored_chunk.document_id,
                    document_name=stored_chunk.document_name,
                    source_type=stored_chunk.source_type,
                    chunk_index=stored_chunk.chunk_index,
                    section=stored_chunk.section,
                    page_start=stored_chunk.page_start,
                    page_end=stored_chunk.page_end,
                    text=stored_chunk.text,
                    retrieval_score=hit.score,
                    rank=rank,
                )
            )

        return RetrievedContextBatch(
            contexts=tuple(contexts),
            missing_chunk_db_ids=tuple(missing_chunk_db_ids),
        )
