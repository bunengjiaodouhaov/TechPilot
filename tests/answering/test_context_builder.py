import pytest

from app.answering.context_builder import ContextBuilder
from app.answering.dto import RetrievedContext


def make_context(
    *,
    chunk_db_id: int,
    rank: int,
    text: str = "Chunk text",
    chunk_id: str | None = None,
    page_start: int | None = 1,
    page_end: int | None = 1,
    section: str | None = "Introduction",
) -> RetrievedContext:
    return RetrievedContext(
        chunk_db_id=chunk_db_id,
        chunk_id=chunk_id or f"chunk-{chunk_db_id}",
        document_id=10,
        document_name="example.pdf",
        source_type="pdf",
        chunk_index=rank - 1,
        section=section,
        page_start=page_start,
        page_end=page_end,
        text=text,
        retrieval_score=1.0 / rank,
        rank=rank,
    )


def test_build_orders_sources_by_rank() -> None:
    builder = ContextBuilder(max_characters=10_000)

    result = builder.build(
        contexts=[
            make_context(chunk_db_id=2, rank=2),
            make_context(chunk_db_id=1, rank=1),
        ]
    )

    assert [
        source.context.chunk_db_id
        for source in result.sources
    ] == [1, 2]

    assert [
        source.source_id
        for source in result.sources
    ] == ["SOURCE_1", "SOURCE_2"]


def test_build_deduplicates_by_database_chunk_id() -> None:
    builder = ContextBuilder(max_characters=10_000)

    result = builder.build(
        contexts=[
            make_context(
                chunk_db_id=1,
                rank=1,
                text="First occurrence",
            ),
            make_context(
                chunk_db_id=1,
                rank=2,
                text="Duplicate occurrence",
            ),
        ]
    )

    assert len(result.sources) == 1
    assert result.sources[0].included_text == "First occurrence"
    assert result.omitted_count == 0


def test_build_stops_when_next_complete_chunk_exceeds_budget() -> None:
    first = make_context(
        chunk_db_id=1,
        rank=1,
        text="A" * 20,
    )
    second = make_context(
        chunk_db_id=2,
        rank=2,
        text="B" * 20,
    )

    first_only = ContextBuilder(
        max_characters=10_000
    ).build(contexts=[first])

    builder = ContextBuilder(
        max_characters=first_only.character_count,
    )

    result = builder.build(
        contexts=[first, second],
    )

    assert len(result.sources) == 1
    assert result.sources[0].context.chunk_db_id == 1
    assert result.omitted_count == 1
    assert "B" * 20 not in result.prompt_context


def test_build_does_not_skip_oversized_higher_ranked_chunk() -> None:
    first = make_context(
        chunk_db_id=1,
        rank=1,
        text="A",
    )
    oversized = make_context(
        chunk_db_id=2,
        rank=2,
        text="B" * 100,
    )
    smaller = make_context(
        chunk_db_id=3,
        rank=3,
        text="C",
    )

    first_only = ContextBuilder(
        max_characters=10_000
    ).build(contexts=[first])

    builder = ContextBuilder(
        max_characters=first_only.character_count,
    )

    result = builder.build(
        contexts=[first, oversized, smaller],
    )

    assert [
        source.context.chunk_db_id
        for source in result.sources
    ] == [1]
    assert result.omitted_count == 2


def test_build_returns_empty_context_when_first_chunk_does_not_fit() -> None:
    builder = ContextBuilder(max_characters=10)

    result = builder.build(
        contexts=[
            make_context(
                chunk_db_id=1,
                rank=1,
                text="A" * 100,
            )
        ]
    )

    assert result.prompt_context == ""
    assert result.sources == ()
    assert result.omitted_count == 1
    assert result.character_count == 0


def test_build_formats_source_metadata() -> None:
    builder = ContextBuilder(max_characters=10_000)

    result = builder.build(
        contexts=[
            make_context(
                chunk_db_id=1,
                rank=1,
                text="Evidence text",
                chunk_id="stable-chunk-id",
                page_start=2,
                page_end=4,
                section="Transactions",
            )
        ]
    )

    assert "[SOURCE_1]" in result.prompt_context
    assert "document: example.pdf" in result.prompt_context
    assert "page: 2-4" in result.prompt_context
    assert "section: Transactions" in result.prompt_context
    assert "chunk_id: stable-chunk-id" in result.prompt_context
    assert "content:\nEvidence text" in result.prompt_context


@pytest.mark.parametrize(
    ("page_start", "page_end", "expected"),
    [
        (None, None, "page: N/A"),
        (1, None, "page: 1"),
        (None, 2, "page: 2"),
        (3, 3, "page: 3"),
        (3, 5, "page: 3-5"),
    ],
)
def test_build_formats_page_ranges(
    page_start: int | None,
    page_end: int | None,
    expected: str,
) -> None:
    builder = ContextBuilder(max_characters=10_000)

    result = builder.build(
        contexts=[
            make_context(
                chunk_db_id=1,
                rank=1,
                page_start=page_start,
                page_end=page_end,
            )
        ]
    )

    assert expected in result.prompt_context


def test_build_uses_na_for_missing_section() -> None:
    builder = ContextBuilder(max_characters=10_000)

    result = builder.build(
        contexts=[
            make_context(
                chunk_db_id=1,
                rank=1,
                section=None,
            )
        ]
    )

    assert "section: N/A" in result.prompt_context


def test_character_count_matches_prompt_length() -> None:
    builder = ContextBuilder(max_characters=10_000)

    result = builder.build(
        contexts=[
            make_context(chunk_db_id=1, rank=1),
            make_context(chunk_db_id=2, rank=2),
        ]
    )

    assert result.character_count == len(result.prompt_context)


@pytest.mark.parametrize(
    "max_characters",
    [0, -1],
)
def test_constructor_rejects_invalid_budget(
    max_characters: int,
) -> None:
    with pytest.raises(
        ValueError,
        match="max_characters must be greater than zero",
    ):
        ContextBuilder(max_characters=max_characters)
