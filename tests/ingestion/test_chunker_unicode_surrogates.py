from app.ingestion.chunker import StructureAwareChunker
from app.ingestion.schemas import ParsedDocument, ParsedElement


def _document(text: str) -> ParsedDocument:
    return ParsedDocument(
        title="unicode regression fixture",
        file_type="pdf",
        file_size=1,
        elements=(
            ParsedElement(
                text=text,
                element_type="paragraph",
                source_metadata={"page_start": 1, "page_end": 1},
            ),
        ),
    )


def test_pdf_chunker_normalizes_lone_surrogate() -> None:
    chunks = StructureAwareChunker(max_chars=1200).chunk(
        _document("equation prefix \ud835 suffix")
    )

    assert len(chunks) == 1
    assert "\ud835" not in chunks[0].text
    assert chunks[0].text.encode("utf-8")
    assert len(chunks[0].chunk_id) == 64


def test_pdf_chunker_removes_nul_before_persistence() -> None:
    chunks = StructureAwareChunker(max_chars=1200).chunk(
        _document("prefix\x00middle\x00suffix")
    )

    assert len(chunks) == 1
    assert "\x00" not in chunks[0].text
    assert chunks[0].text == "prefixmiddlesuffix"
    assert chunks[0].text.encode("utf-8")


def test_unicode_normalization_is_deterministic() -> None:
    chunker = StructureAwareChunker(max_chars=1200)
    document = _document("A\ud835B\x00C")

    first = chunker.chunk(document)
    second = chunker.chunk(document)

    assert first[0].chunk_id == second[0].chunk_id
    assert first[0].text == second[0].text
    assert "\ud835" not in first[0].text
    assert "\x00" not in first[0].text
