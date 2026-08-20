import pytest

from app.retrieval.rrf import reciprocal_rank_fusion


def test_rrf_fuses_dense_and_bm25_by_chunk_identity() -> None:
    results = reciprocal_rank_fusion(
        dense_chunk_ids=[
            "chunk-a",
            "chunk-b",
        ],
        bm25_chunk_ids=[
            "chunk-b",
            "chunk-c",
        ],
        k=60,
    )

    assert [
        result.chunk_id
        for result in results
    ] == [
        "chunk-b",
        "chunk-a",
        "chunk-c",
    ]

    chunk_b = results[0]

    assert chunk_b.dense_rank == 2
    assert chunk_b.bm25_rank == 1
    assert chunk_b.score == pytest.approx(
        1 / 62 + 1 / 61
    )


def test_rrf_does_not_double_count_duplicate_within_one_route() -> None:
    results = reciprocal_rank_fusion(
        dense_chunk_ids=[
            "chunk-a",
            "chunk-a",
        ],
        bm25_chunk_ids=[],
        k=60,
    )

    assert len(results) == 1

    result = results[0]

    assert result.chunk_id == "chunk-a"
    assert result.dense_rank == 1
    assert result.bm25_rank is None
    assert result.score == pytest.approx(
        1 / 61
    )


def test_rrf_preserves_candidates_found_by_only_one_retriever() -> None:
    results = reciprocal_rank_fusion(
        dense_chunk_ids=[
            "dense-only",
        ],
        bm25_chunk_ids=[
            "bm25-only",
        ],
        k=60,
    )

    by_chunk_id = {
        result.chunk_id: result
        for result in results
    }

    assert set(by_chunk_id) == {
        "dense-only",
        "bm25-only",
    }

    assert (
        by_chunk_id["dense-only"].dense_rank
        == 1
    )
    assert (
        by_chunk_id["dense-only"].bm25_rank
        is None
    )

    assert (
        by_chunk_id["bm25-only"].dense_rank
        is None
    )
    assert (
        by_chunk_id["bm25-only"].bm25_rank
        == 1
    )


def test_rrf_uses_deterministic_chunk_id_tiebreak() -> None:
    results = reciprocal_rank_fusion(
        dense_chunk_ids=[
            "chunk-b",
        ],
        bm25_chunk_ids=[
            "chunk-a",
        ],
        k=60,
    )

    assert [
        result.chunk_id
        for result in results
    ] == [
        "chunk-a",
        "chunk-b",
    ]


@pytest.mark.parametrize(
    "k",
    [
        0,
        -1,
    ],
)
def test_rrf_rejects_invalid_k(
    k: int,
) -> None:
    with pytest.raises(
        ValueError,
        match="k must be greater than zero",
    ):
        reciprocal_rank_fusion(
            dense_chunk_ids=[],
            bm25_chunk_ids=[],
            k=k,
        )


def test_rrf_rejects_empty_chunk_identity() -> None:
    with pytest.raises(
        ValueError,
        match="chunk_id must not be empty",
    ):
        reciprocal_rank_fusion(
            dense_chunk_ids=[
                " ",
            ],
            bm25_chunk_ids=[],
        )