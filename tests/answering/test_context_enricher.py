from app.answering.context_enricher import ContextEnricher
from app.answering.dto import StoredChunk
from app.retrieval.dto import (
    ChunkVectorPayload,
    VectorSearchHit,
)


def make_hit(
    *,
    point_id: int,
    score: float,
) -> VectorSearchHit:
    return VectorSearchHit(
        point_id=point_id,
        score=score,
        payload=ChunkVectorPayload(
            workspace_id=1,
            document_id=10,
            chunk_id=f"payload-chunk-{point_id}",
            chunk_index=0,
            section="Payload section",
            document_name="payload.pdf",
            source_type="pdf",
            page_start=1,
            page_end=1,
        ),
    )


def make_stored_chunk(
    *,
    chunk_db_id: int,
) -> StoredChunk:
    return StoredChunk(
        chunk_db_id=chunk_db_id,
        chunk_id=f"stored-chunk-{chunk_db_id}",
        document_id=20,
        document_name="stored.pdf",
        source_type="pdf",
        chunk_index=3,
        section="Stored section",
        page_start=4,
        page_end=5,
        text="Authoritative PostgreSQL text.",
    )


def test_enrich_preserves_hit_order_and_assigns_rank() -> None:
    enricher = ContextEnricher()

    result = enricher.enrich(
        hits=[
            make_hit(point_id=20, score=0.91),
            make_hit(point_id=10, score=0.83),
        ],
        stored_chunks={
            10: make_stored_chunk(chunk_db_id=10),
            20: make_stored_chunk(chunk_db_id=20),
        },
    )

    assert [
        context.chunk_db_id
        for context in result.contexts
    ] == [20, 10]

    assert [
        context.rank
        for context in result.contexts
    ] == [1, 2]

    assert [
        context.retrieval_score
        for context in result.contexts
    ] == [0.91, 0.83]


def test_enrich_uses_postgresql_as_authoritative_source() -> None:
    enricher = ContextEnricher()

    result = enricher.enrich(
        hits=[
            make_hit(point_id=10, score=0.91),
        ],
        stored_chunks={
            10: make_stored_chunk(chunk_db_id=10),
        },
    )

    context = result.contexts[0]

    assert context.chunk_id == "stored-chunk-10"
    assert context.document_id == 20
    assert context.document_name == "stored.pdf"
    assert context.section == "Stored section"
    assert context.page_start == 4
    assert context.page_end == 5
    assert context.text == "Authoritative PostgreSQL text."


def test_enrich_records_missing_chunk_ids() -> None:
    enricher = ContextEnricher()

    result = enricher.enrich(
        hits=[
            make_hit(point_id=10, score=0.91),
            make_hit(point_id=20, score=0.83),
        ],
        stored_chunks={
            10: make_stored_chunk(chunk_db_id=10),
        },
    )

    assert len(result.contexts) == 1
    assert result.contexts[0].chunk_db_id == 10
    assert result.missing_chunk_db_ids == (20,)


def test_enrich_returns_empty_batch_for_empty_input() -> None:
    enricher = ContextEnricher()

    result = enricher.enrich(
        hits=[],
        stored_chunks={},
    )

    assert result.contexts == ()
    assert result.missing_chunk_db_ids == ()
