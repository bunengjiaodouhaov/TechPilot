from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from app.ingestion.schemas import ChunkData, ParsedDocument, ParsedElement


@dataclass(frozen=True, slots=True)
class _ChunkCandidate:
    text: str
    page_start: int | None
    page_end: int | None
    section: str | None
    metadata: dict[str, Any]


class StructureAwareChunker:
    """Create retrieval chunks while preserving parser-provided structure."""

    def __init__(self, max_chars: int = 1200) -> None:
        if max_chars < 100:
            raise ValueError("max_chars must be at least 100.")
        self.max_chars = max_chars

    def chunk(self, document: ParsedDocument) -> tuple[ChunkData, ...]:
        if document.file_type == "markdown":
            candidates = self._chunk_markdown(document.elements)
        elif document.file_type == "pdf":
            candidates = self._chunk_pdf(document.elements)
        else:
            raise ValueError(
                f"Unsupported parsed file type: {document.file_type}"
            )

        return self._finalize(candidates)

    def _chunk_markdown(
        self,
        elements: tuple[ParsedElement, ...],
    ) -> list[_ChunkCandidate]:
        candidates: list[_ChunkCandidate] = []
        current_path: tuple[str, ...] = ()
        paragraph_buffer: list[ParsedElement] = []

        def flush_paragraphs() -> None:
            nonlocal paragraph_buffer

            if not paragraph_buffer:
                return

            section = " > ".join(current_path) if current_path else None
            group: list[ParsedElement] = []

            for element in paragraph_buffer:
                proposed_text = self._markdown_text(
                    section,
                    [item.text for item in group + [element]],
                )

                if group and len(proposed_text) > self.max_chars:
                    candidates.append(
                        self._markdown_candidate(section, group)
                    )
                    group = []

                single_text = self._markdown_text(
                    section,
                    [element.text],
                )

                if len(single_text) > self.max_chars:
                    if group:
                        candidates.append(
                            self._markdown_candidate(section, group)
                        )
                        group = []

                    candidates.extend(
                        self._split_markdown_element(section, element)
                    )
                else:
                    group.append(element)

            if group:
                candidates.append(
                    self._markdown_candidate(section, group)
                )

            paragraph_buffer = []

        for element in elements:
            if element.element_type == "heading":
                flush_paragraphs()

                current_path = tuple(
                    str(item)
                    for item in element.source_metadata.get(
                        "heading_path",
                        [],
                    )
                )
                # Headings are structural context, not standalone
                # retrieval chunks. They are preserved in `section`,
                # `heading_path`, and injected into body chunk text.
                continue

            if element.element_type == "paragraph":
                element_path = tuple(
                    str(item)
                    for item in element.source_metadata.get(
                        "heading_path",
                        current_path,
                    )
                )

                if element_path != current_path:
                    flush_paragraphs()
                    current_path = element_path

                paragraph_buffer.append(element)

        flush_paragraphs()
        return candidates

    def _markdown_candidate(
        self,
        section: str | None,
        elements: list[ParsedElement],
    ) -> _ChunkCandidate:
        starts = [
            int(value)
            for element in elements
            if (
                value := element.source_metadata.get("line_start")
            ) is not None
        ]
        ends = [
            int(value)
            for element in elements
            if (
                value := element.source_metadata.get("line_end")
            ) is not None
        ]

        return _ChunkCandidate(
            text=self._markdown_text(
                section,
                [element.text for element in elements],
            ),
            page_start=None,
            page_end=None,
            section=section,
            metadata={
                "element_types": [
                    element.element_type for element in elements
                ],
                "heading_path": (
                    section.split(" > ")
                    if section
                    else []
                ),
                "heading_injected": bool(section),
                "line_start": min(starts) if starts else None,
                "line_end": max(ends) if ends else None,
            },
        )

    def _split_markdown_element(
        self,
        section: str | None,
        element: ParsedElement,
    ) -> list[_ChunkCandidate]:
        prefix = f"{section}\n\n" if section else ""
        available = self.max_chars - len(prefix)

        if available <= 0:
            raise ValueError(
                "max_chars is too small for the heading context."
            )

        return [
            _ChunkCandidate(
                text=f"{prefix}{piece}",
                page_start=None,
                page_end=None,
                section=section,
                metadata={
                    "element_types": [element.element_type],
                    "heading_path": (
                        section.split(" > ")
                        if section
                        else []
                    ),
                    "heading_injected": bool(section),
                    "line_start": element.source_metadata.get(
                        "line_start"
                    ),
                    "line_end": element.source_metadata.get(
                        "line_end"
                    ),
                    "part_index": part_index,
                },
            )
            for part_index, piece in enumerate(
                self._split_text(element.text, available)
            )
        ]

    def _chunk_pdf(
        self,
        elements: tuple[ParsedElement, ...],
    ) -> list[_ChunkCandidate]:
        atomic: list[_ChunkCandidate] = []

        for element in elements:
            page_start = self._optional_int(
                element.source_metadata.get("page_start")
            )
            page_end = self._optional_int(
                element.source_metadata.get("page_end")
            )

            for part_index, piece in enumerate(
                self._split_text(element.text, self.max_chars)
            ):
                atomic.append(
                    _ChunkCandidate(
                        text=piece,
                        page_start=page_start,
                        page_end=page_end,
                        section=None,
                        metadata={
                            "element_types": [
                                element.element_type
                            ],
                            "part_index": part_index,
                        },
                    )
                )

        merged: list[_ChunkCandidate] = []
        buffer: list[_ChunkCandidate] = []

        def flush() -> None:
            nonlocal buffer

            if not buffer:
                return

            starts = [
                item.page_start
                for item in buffer
                if item.page_start is not None
            ]
            ends = [
                item.page_end
                for item in buffer
                if item.page_end is not None
            ]

            merged.append(
                _ChunkCandidate(
                    text="\n\n".join(
                        item.text for item in buffer
                    ),
                    page_start=min(starts) if starts else None,
                    page_end=max(ends) if ends else None,
                    section=None,
                    metadata={
                        "element_types": [
                            element_type
                            for item in buffer
                            for element_type in item.metadata[
                                "element_types"
                            ]
                        ]
                    },
                )
            )
            buffer = []

        for candidate in atomic:
            proposed = "\n\n".join(
                [item.text for item in buffer]
                + [candidate.text]
            )

            if buffer and len(proposed) > self.max_chars:
                flush()

            buffer.append(candidate)

        flush()
        return merged

    def _finalize(
        self,
        candidates: list[_ChunkCandidate],
    ) -> tuple[ChunkData, ...]:
        occurrences: defaultdict[str, int] = defaultdict(int)
        chunks: list[ChunkData] = []

        for chunk_index, candidate in enumerate(candidates):
            identity = "\n".join(
                [
                    candidate.section or "",
                    str(candidate.page_start or ""),
                    str(candidate.page_end or ""),
                    " ".join(candidate.text.split()),
                ]
            )

            occurrence = occurrences[identity]
            occurrences[identity] += 1

            chunk_id = sha256(
                (
                    f"{identity}\n"
                    f"occurrence:{occurrence}"
                ).encode("utf-8")
            ).hexdigest()

            chunks.append(
                ChunkData(
                    chunk_id=chunk_id,
                    chunk_index=chunk_index,
                    text=candidate.text,
                    page_start=candidate.page_start,
                    page_end=candidate.page_end,
                    section=candidate.section,
                    char_count=len(candidate.text),
                    metadata=candidate.metadata,
                )
            )

        return tuple(chunks)

    @staticmethod
    def _markdown_text(
        section: str | None,
        paragraphs: list[str],
    ) -> str:
        body = "\n\n".join(
            item.strip()
            for item in paragraphs
            if item.strip()
        )

        return (
            f"{section}\n\n{body}"
            if section
            else body
        )

    @staticmethod
    def _optional_int(value: object) -> int | None:
        return int(value) if value is not None else None

    @staticmethod
    def _split_text(
        text: str,
        max_chars: int,
    ) -> list[str]:
        remaining = text.strip()

        if not remaining:
            return []

        pieces: list[str] = []

        while len(remaining) > max_chars:
            split_at = remaining.rfind(
                " ",
                0,
                max_chars + 1,
            )

            if split_at <= 0:
                split_at = max_chars

            pieces.append(
                remaining[:split_at].strip()
            )
            remaining = remaining[split_at:].strip()

        if remaining:
            pieces.append(remaining)

        return pieces
