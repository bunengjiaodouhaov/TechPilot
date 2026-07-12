from app.ingestion.chunker import StructureAwareChunker
from app.ingestion.schemas import ParsedDocument, ParsedElement


def test_markdown_chunker_uses_heading_as_body_context() -> None:
    document = ParsedDocument(
        title="Guide",
        file_type="markdown",
        file_size=100,
        elements=(
            ParsedElement(
                text="Authentication",
                element_type="heading",
                source_metadata={
                    "heading_path": ["Authentication"],
                    "heading_level": 1,
                    "line_start": 1,
                    "line_end": 1,
                },
            ),
            ParsedElement(
                text="Use an API key.",
                element_type="paragraph",
                source_metadata={
                    "heading_path": ["Authentication"],
                    "line_start": 3,
                    "line_end": 3,
                },
            ),
            ParsedElement(
                text="Rotate keys regularly.",
                element_type="paragraph",
                source_metadata={
                    "heading_path": ["Authentication"],
                    "line_start": 5,
                    "line_end": 5,
                },
            ),
        ),
    )

    chunks = StructureAwareChunker(
        max_chars=200
    ).chunk(document)

    assert len(chunks) == 1
    assert chunks[0].text == (
        "Authentication\n\n"
        "Use an API key.\n\n"
        "Rotate keys regularly."
    )
    assert chunks[0].section == "Authentication"
    assert chunks[0].metadata["heading_path"] == [
        "Authentication"
    ]
    assert chunks[0].metadata["heading_injected"] is True
    assert chunks[0].char_count == len(chunks[0].text)


def test_heading_without_body_does_not_create_empty_chunk() -> None:
    document = ParsedDocument(
        title="Guide",
        file_type="markdown",
        file_size=20,
        elements=(
            ParsedElement(
                text="Empty Section",
                element_type="heading",
                source_metadata={
                    "heading_path": ["Empty Section"],
                    "heading_level": 1,
                },
            ),
        ),
    )

    chunks = StructureAwareChunker(
        max_chars=200
    ).chunk(document)

    assert chunks == ()

def test_pdf_chunker_merges_pages_and_preserves_page_range() -> None:
    document = ParsedDocument(
        title="Guide",
        file_type="pdf",
        file_size=100,
        elements=(
            ParsedElement(
                text="Page one.",
                element_type="page_text",
                source_metadata={
                    "page_start": 1,
                    "page_end": 1,
                },
            ),
            ParsedElement(
                text="Page two.",
                element_type="page_text",
                source_metadata={
                    "page_start": 2,
                    "page_end": 2,
                },
            ),
        ),
    )

    chunks = StructureAwareChunker(
        max_chars=100
    ).chunk(document)

    assert len(chunks) == 1
    assert chunks[0].text == (
        "Page one.\n\nPage two."
    )
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 2


def test_chunk_ids_are_deterministic() -> None:
    document = ParsedDocument(
        title="Guide",
        file_type="markdown",
        file_size=100,
        elements=(
            ParsedElement(
                text="Stable",
                element_type="heading",
                source_metadata={
                    "heading_path": ["Stable"],
                    "heading_level": 1,
                },
            ),
            ParsedElement(
                text="Stable content.",
                element_type="paragraph",
                source_metadata={
                    "heading_path": ["Stable"],
                },
            ),
        ),
    )

    chunker = StructureAwareChunker(max_chars=200)

    first = chunker.chunk(document)
    second = chunker.chunk(document)

    assert [
        chunk.chunk_id for chunk in first
    ] == [
        chunk.chunk_id for chunk in second
    ]
    assert all(
        len(chunk.chunk_id) == 64
        for chunk in first
    )


def test_stable_chunk_id_does_not_depend_on_global_index() -> None:
    target = ParsedElement(
        text="Stable content.",
        element_type="paragraph",
        source_metadata={
            "heading_path": ["Stable"],
        },
    )

    original = ParsedDocument(
        title="Guide",
        file_type="markdown",
        file_size=100,
        elements=(
            ParsedElement(
                text="Stable",
                element_type="heading",
                source_metadata={
                    "heading_path": ["Stable"],
                    "heading_level": 1,
                },
            ),
            target,
        ),
    )

    with_unrelated_section = ParsedDocument(
        title="Guide",
        file_type="markdown",
        file_size=120,
        elements=(
            ParsedElement(
                text="Introduction",
                element_type="heading",
                source_metadata={
                    "heading_path": ["Introduction"],
                    "heading_level": 1,
                },
            ),
            ParsedElement(
                text="New content.",
                element_type="paragraph",
                source_metadata={
                    "heading_path": ["Introduction"],
                },
            ),
            ParsedElement(
                text="Stable",
                element_type="heading",
                source_metadata={
                    "heading_path": ["Stable"],
                    "heading_level": 1,
                },
            ),
            target,
        ),
    )

    chunker = StructureAwareChunker(max_chars=200)

    original_chunks = chunker.chunk(original)
    changed_chunks = chunker.chunk(with_unrelated_section)

    original_target = next(
        chunk
        for chunk in original_chunks
        if chunk.text.endswith("Stable content.")
    )
    changed_target = next(
        chunk
        for chunk in changed_chunks
        if chunk.text.endswith("Stable content.")
    )

    assert original_target.chunk_index != changed_target.chunk_index
    assert original_target.chunk_id == changed_target.chunk_id
