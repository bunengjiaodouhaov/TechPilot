import pytest

from app.answering.answer_service import (
    AnswerService,
    InvalidLLMCitationError,
)
from app.answering.context_builder import ContextBuilder
from app.answering.dto import (
    BuiltContext,
    Citation,
    LLMAnswer,
    RetrievedContext,
)
from app.answering.evidence_dto import (
    EvidenceState,
    EvidenceVerificationResult,
)
from app.ingestion.chunker import StructureAwareChunker
from app.ingestion.parsers.markdown import MarkdownParser
from app.ingestion.parsers.pdf import PDFParser
from app.ingestion.schemas import ChunkData, ParseInput


def _to_retrieved_context(
    *,
    chunk: ChunkData,
    source_type: str,
    document_name: str,
    chunk_db_id: int = 1,
    rank: int = 1,
) -> RetrievedContext:
    return RetrievedContext(
        chunk_db_id=chunk_db_id,
        chunk_id=chunk.chunk_id,
        document_id=10,
        document_name=document_name,
        source_type=source_type,
        chunk_index=chunk.chunk_index,
        section=chunk.section,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        text=chunk.text,
        retrieval_score=1.0 / rank,
        rank=rank,
    )


def _sufficient_verification(
    *source_ids: str,
) -> EvidenceVerificationResult:
    return EvidenceVerificationResult(
        state=EvidenceState.SUFFICIENT,
        reasons=(),
        supporting_source_ids=tuple(source_ids),
        conflicting_source_ids=(),
        explanation="Evidence supports the test answer.",
    )


def _build_citation(
    context: RetrievedContext,
) -> tuple[BuiltContext, Citation]:
    built_context = ContextBuilder(max_characters=10_000).build(
        contexts=[context]
    )

    answer = AnswerService._build_answer(
        question="Question",
        llm_answer=LLMAnswer(
            text="Answer",
            cited_source_ids=("SOURCE_1",),
            refused=False,
        ),
        built_context=built_context,
        verification=_sufficient_verification("SOURCE_1"),
    )

    return built_context, answer.citations[0]


def test_markdown_heading_path_reaches_server_built_citation() -> None:
    content = (
        "# TechPilot\n\n"
        "## Storage\n\n"
        "PostgreSQL is the authoritative source of chunk text.\n"
    ).encode("utf-8")

    parsed = MarkdownParser().parse(
        ParseInput(
            filename="architecture.md",
            content_type="text/markdown",
            file_size=len(content),
            file_bytes=content,
        )
    )
    chunks = StructureAwareChunker().chunk(parsed)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.section == "TechPilot > Storage"
    assert chunk.metadata["heading_path"] == ["TechPilot", "Storage"]

    built_context, citation = _build_citation(
        _to_retrieved_context(
            chunk=chunk,
            source_type="markdown",
            document_name="architecture.md",
        )
    )

    assert "section: TechPilot > Storage" in built_context.prompt_context
    assert citation.document_name == "architecture.md"
    assert citation.section == "TechPilot > Storage"
    assert citation.page_start is None
    assert citation.page_end is None
    assert citation.quote == chunk.text


def test_pdf_page_range_reaches_server_built_citation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePage:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class FakeReader:
        is_encrypted = False
        metadata = None
        pages = (
            FakePage("Evidence from page one."),
            FakePage("Evidence from page two."),
        )

    monkeypatch.setattr(
        "app.ingestion.parsers.pdf.PdfReader",
        lambda _: FakeReader(),
    )

    file_bytes = b"fake-pdf-bytes"
    parsed = PDFParser().parse(
        ParseInput(
            filename="database.pdf",
            content_type="application/pdf",
            file_size=len(file_bytes),
            file_bytes=file_bytes,
        )
    )
    chunks = StructureAwareChunker(max_chars=100).chunk(parsed)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.page_start == 1
    assert chunk.page_end == 2

    built_context, citation = _build_citation(
        _to_retrieved_context(
            chunk=chunk,
            source_type="pdf",
            document_name="database.pdf",
        )
    )

    assert "page: 1-2" in built_context.prompt_context
    assert citation.document_name == "database.pdf"
    assert citation.page_start == 1
    assert citation.page_end == 2
    assert citation.quote == chunk.text


def test_context_budget_omitted_source_cannot_be_cited() -> None:
    first = RetrievedContext(
        chunk_db_id=1,
        chunk_id="chunk-1",
        document_id=10,
        document_name="first.md",
        source_type="markdown",
        chunk_index=0,
        section="First",
        page_start=None,
        page_end=None,
        text="First evidence.",
        retrieval_score=0.9,
        rank=1,
    )
    second = RetrievedContext(
        chunk_db_id=2,
        chunk_id="chunk-2",
        document_id=11,
        document_name="second.md",
        source_type="markdown",
        chunk_index=0,
        section="Second",
        page_start=None,
        page_end=None,
        text="Second evidence.",
        retrieval_score=0.8,
        rank=2,
    )

    first_only = ContextBuilder(max_characters=10_000).build(
        contexts=[first]
    )
    built_context = ContextBuilder(
        max_characters=first_only.character_count
    ).build(contexts=[first, second])

    assert [
        source.source_id for source in built_context.sources
    ] == ["SOURCE_1"]
    assert built_context.omitted_count == 1
    assert "Second evidence." not in built_context.prompt_context

    with pytest.raises(
        InvalidLLMCitationError,
        match="not verified as supporting: SOURCE_2",
    ):
        AnswerService._build_answer(
            question="Question",
            llm_answer=LLMAnswer(
                text="Unsupported answer",
                cited_source_ids=("SOURCE_2",),
                refused=False,
            ),
            built_context=built_context,
            verification=_sufficient_verification("SOURCE_1"),
        )
