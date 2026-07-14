from dataclasses import FrozenInstanceError

import pytest

from app.retrieval.dto import ChunkVectorPayload, VectorPoint


def make_payload() -> ChunkVectorPayload:
    return ChunkVectorPayload(
        workspace_id=1,
        document_id=10,
        chunk_id="document-10-chunk-0",
        chunk_index=0,
        section="Introduction",
        document_name="example.pdf",
        source_type="pdf",
        page_start=1,
        page_end=2,
    )


def test_chunk_vector_payload_stores_expected_metadata() -> None:
    payload = make_payload()

    assert payload.workspace_id == 1
    assert payload.document_id == 10
    assert payload.chunk_id == "document-10-chunk-0"
    assert payload.chunk_index == 0
    assert payload.section == "Introduction"
    assert payload.document_name == "example.pdf"
    assert payload.source_type == "pdf"
    assert payload.page_start == 1
    assert payload.page_end == 2


def test_vector_point_contains_point_id_vector_and_payload() -> None:
    payload = make_payload()

    point = VectorPoint(
        point_id=100,
        vector=[0.1, 0.2, 0.3],
        payload=payload,
    )

    assert point.point_id == 100
    assert point.vector == [0.1, 0.2, 0.3]
    assert point.payload == payload


def test_chunk_vector_payload_is_frozen() -> None:
    payload = make_payload()

    with pytest.raises(FrozenInstanceError):
        payload.workspace_id = 2  # type: ignore[misc]


def test_vector_point_is_frozen() -> None:
    point = VectorPoint(
        point_id=100,
        vector=[0.1, 0.2],
        payload=make_payload(),
    )

    with pytest.raises(FrozenInstanceError):
        point.point_id = 101  # type: ignore[misc]


def test_vector_search_hit_contains_score_and_payload() -> None:
    from app.retrieval.dto import VectorSearchHit

    payload = make_payload()

    hit = VectorSearchHit(
        point_id=100,
        score=0.87,
        payload=payload,
    )

    assert hit.point_id == 100
    assert hit.score == 0.87
    assert hit.payload == payload


def test_vector_search_hit_is_frozen() -> None:
    from app.retrieval.dto import VectorSearchHit

    hit = VectorSearchHit(
        point_id=100,
        score=0.87,
        payload=make_payload(),
    )

    with pytest.raises(FrozenInstanceError):
        hit.score = 0.5  # type: ignore[misc]
