from __future__ import annotations

from scripts.document_retrieval_truth_project import (
    ChunkProjectionInput,
    project_evidence_to_chunks,
)


def test_exact_quote_projects_to_one_chunk() -> None:
    quote = "Organizations should maintain tested incident response plans."
    chunks = [
        ChunkProjectionInput(
            chunk_db_id=1,
            chunk_id="c1",
            document_id=10,
            text="Before incidents, Organizations should maintain tested incident response plans. Roles are documented.",
            page_start=4,
            page_end=4,
            section=None,
        ),
        ChunkProjectionInput(
            chunk_db_id=2,
            chunk_id="c2",
            document_id=10,
            text="Unrelated content on another page.",
            page_start=5,
            page_end=5,
            section=None,
        ),
    ]
    relevant, coverage, method = project_evidence_to_chunks(
        evidence_quote=quote,
        expected_page=4,
        expected_section=None,
        chunks=chunks,
    )
    assert method == "exact_quote"
    assert coverage == 1.0
    assert [item.chunk_db_id for item in relevant] == [1]
    assert relevant[0].relevance_grade == 3


def test_split_quote_projects_across_two_chunks() -> None:
    quote = (
        "organizations should maintain tested incident response plans "
        "and define clear response roles before incidents occur"
    )
    chunks = [
        ChunkProjectionInput(
            chunk_db_id=1,
            chunk_id="c1",
            document_id=10,
            text="organizations should maintain tested incident response plans and define",
            page_start=4,
            page_end=4,
            section=None,
        ),
        ChunkProjectionInput(
            chunk_db_id=2,
            chunk_id="c2",
            document_id=10,
            text="incident response plans and define clear response roles before incidents occur",
            page_start=4,
            page_end=4,
            section=None,
        ),
    ]
    relevant, coverage, method = project_evidence_to_chunks(
        evidence_quote=quote,
        expected_page=4,
        expected_section=None,
        chunks=chunks,
        partial_threshold=0.20,
    )
    assert method == "split_or_partial_quote"
    assert len(relevant) == 2
    assert coverage >= 0.80


def test_page_filter_blocks_identical_text_on_wrong_page() -> None:
    quote = "configuration management plans are generated during development"
    chunks = [
        ChunkProjectionInput(
            chunk_db_id=1,
            chunk_id="c1",
            document_id=10,
            text=quote,
            page_start=8,
            page_end=8,
            section=None,
        )
    ]
    relevant, coverage, method = project_evidence_to_chunks(
        evidence_quote=quote,
        expected_page=7,
        expected_section=None,
        chunks=chunks,
    )
    assert relevant == []
    assert coverage == 0.0
    assert method == "unprojected"
