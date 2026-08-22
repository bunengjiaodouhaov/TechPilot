from __future__ import annotations

from scripts.document_retrieval_truth_project import (
    ChunkProjectionInput,
    normalize_text,
    project_evidence_to_chunks,
)


def _chunk(
    db_id: int,
    text: str,
    *,
    page: int = 4,
    index: int = 0,
) -> ChunkProjectionInput:
    return ChunkProjectionInput(
        chunk_db_id=db_id,
        chunk_id=f"c{db_id}",
        document_id=10,
        text=text,
        page_start=page,
        page_end=page,
        section=None,
        chunk_index=index,
    )


def test_exact_quote_sets_full_coverage_even_for_short_quote() -> None:
    quote = "Security-as-a-Service (SECaaS)"
    relevant, coverage, method = project_evidence_to_chunks(
        evidence_quote=quote,
        expected_page=4,
        expected_section=None,
        chunks=[
            _chunk(
                1,
                "The architecture can consume Security-as-a-Service (SECaaS) "
                "from a cloud provider.",
            )
        ],
    )
    assert method == "exact_quote"
    assert coverage == 1.0
    assert relevant[0].evidence_coverage == 1.0
    assert relevant[0].evidence_shingle_indices == (0,)


def test_nfkc_and_pdf_hyphen_normalization() -> None:
    assert normalize_text("diﬀerential privacy- enhancing") == (
        "differential privacy-enhancing"
    )


def test_exact_quote_across_adjacent_chunks_reaches_full_union_coverage() -> None:
    quote = (
        "organizations should maintain tested incident response plans "
        "and define clear response roles before incidents occur"
    )
    chunks = [
        _chunk(
            1,
            "prefix organizations should maintain tested incident response plans "
            "and define",
            index=1,
        ),
        _chunk(
            2,
            "clear response roles before incidents occur suffix",
            index=2,
        ),
    ]
    relevant, coverage, method = project_evidence_to_chunks(
        evidence_quote=quote,
        expected_page=4,
        expected_section=None,
        chunks=chunks,
    )
    assert method == "exact_quote_across_chunks"
    assert coverage == 1.0
    assert [item.chunk_db_id for item in relevant] == [1, 2]


def test_split_projection_keeps_small_boundary_contributor() -> None:
    quote = (
        "alpha beta gamma delta epsilon zeta eta theta iota kappa "
        "lambda mu nu xi omicron"
    )
    chunks = [
        _chunk(
            1,
            "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda",
            index=1,
        ),
        _chunk(
            2,
            "theta iota kappa lambda mu nu xi omicron extra",
            index=2,
        ),
    ]
    relevant, coverage, method = project_evidence_to_chunks(
        evidence_quote=quote,
        expected_page=4,
        expected_section=None,
        chunks=chunks,
        partial_threshold=0.30,
    )
    assert method in {"exact_quote_across_chunks", "split_or_partial_quote"}
    assert coverage >= 0.80
    assert len(relevant) == 2


def test_page_filter_still_blocks_wrong_page() -> None:
    quote = "configuration management plans are generated during development"
    relevant, coverage, method = project_evidence_to_chunks(
        evidence_quote=quote,
        expected_page=7,
        expected_section=None,
        chunks=[_chunk(1, quote, page=8)],
    )
    assert relevant == []
    assert coverage == 0.0
    assert method == "unprojected"
