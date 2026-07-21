from dataclasses import dataclass


@dataclass(frozen=True)
class StoredChunk:
    """One persisted chunk loaded from PostgreSQL with document metadata."""

    chunk_db_id: int
    chunk_id: str
    document_id: int
    document_name: str
    source_type: str
    chunk_index: int
    section: str | None
    page_start: int | None
    page_end: int | None
    text: str


@dataclass(frozen=True)
class RetrievedContext:
    """One dense-retrieval hit enriched with PostgreSQL chunk text."""

    chunk_db_id: int
    chunk_id: str
    document_id: int
    document_name: str
    source_type: str
    chunk_index: int
    section: str | None
    page_start: int | None
    page_end: int | None
    text: str
    retrieval_score: float
    rank: int


@dataclass(frozen=True)
class RetrievedContextBatch:
    """Retrieved contexts plus any missing PostgreSQL chunk IDs."""

    contexts: tuple[RetrievedContext, ...]
    missing_chunk_db_ids: tuple[int, ...]


@dataclass(frozen=True)
class BuiltContextSource:
    """A source that was actually included in the LLM prompt context."""

    source_id: str
    context: RetrievedContext
    included_text: str


@dataclass(frozen=True)
class BuiltContext:
    """Prompt-ready context plus its authoritative source mapping."""

    prompt_context: str
    sources: tuple[BuiltContextSource, ...]
    omitted_count: int
    character_count: int


@dataclass(frozen=True)
class Citation:
    """Server-built citation derived from an included context source."""

    chunk_id: str
    document_id: int
    document_name: str
    page_start: int | None
    page_end: int | None
    section: str | None
    quote: str


@dataclass(frozen=True)
class LLMAnswer:
    """Provider-neutral LLM output using only internal source identifiers."""

    text: str
    cited_source_ids: tuple[str, ...]
    refused: bool


@dataclass(frozen=True)
class Answer:
    """Final trustworthy answer returned by the answering service."""

    question: str
    text: str
    citations: tuple[Citation, ...]
    refused: bool
