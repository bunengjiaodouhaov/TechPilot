from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ParseInput:
    """Validated file data passed from the ingestion boundary to a parser."""

    filename: str
    content_type: str
    file_size: int
    file_bytes: bytes


@dataclass(frozen=True, slots=True)
class ParsedElement:
    """One structure-preserving element extracted from a source document."""

    text: str
    element_type: str
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Parser-independent intermediate representation used by chunkers."""

    title: str
    file_type: str
    file_size: int
    elements: tuple[ParsedElement, ...]
    metadata: dict[str, Any] = field(default_factory=dict)



@dataclass(frozen=True, slots=True)
class ChunkData:
    """Chunker output ready for persistence and later embedding."""

    chunk_id: str
    chunk_index: int
    text: str
    page_start: int | None
    page_end: int | None
    section: str | None
    char_count: int
    metadata: dict[str, Any] = field(default_factory=dict)
