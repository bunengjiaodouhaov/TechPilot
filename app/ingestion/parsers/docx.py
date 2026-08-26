from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile

from docx import Document
from docx.document import Document as WordDocument
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.ingestion.parsers.base import BaseParser
from app.ingestion.schemas import ParseInput, ParsedDocument, ParsedElement


_HEADING_PATTERN = re.compile(r"^Heading\s*([1-6])$", re.IGNORECASE)


class DOCXParser(BaseParser):
    """Parse Office Open XML Word documents while preserving structure."""

    def parse(self, parse_input: ParseInput) -> ParsedDocument:
        try:
            document = Document(BytesIO(parse_input.file_bytes))
        except (PackageNotFoundError, BadZipFile, KeyError, ValueError) as exc:
            raise ValueError("DOCX file is invalid or unreadable.") from exc

        elements: list[ParsedElement] = []
        heading_stack: list[str] = []
        document_title: str | None = None
        paragraph_index = 0
        table_index = 0
        block_index = 0

        for block in self._iter_blocks(document):
            block_index += 1

            if isinstance(block, Paragraph):
                paragraph_index += 1
                text = self._clean_text(block.text)
                if not text:
                    continue

                if self._is_title(block) and document_title is None:
                    document_title = text

                heading_level = self._heading_level(block)
                if heading_level is not None:
                    heading_stack[:] = heading_stack[: heading_level - 1]
                    heading_stack.append(text)
                    elements.append(
                        ParsedElement(
                            text=text,
                            element_type="heading",
                            source_metadata={
                                "heading_path": list(heading_stack),
                                "heading_level": heading_level,
                                "block_index": block_index,
                                "paragraph_index": paragraph_index,
                            },
                        )
                    )
                    if heading_level == 1 and document_title is None:
                        document_title = text
                    continue

                elements.append(
                    ParsedElement(
                        text=text,
                        element_type="paragraph",
                        source_metadata={
                            "heading_path": list(heading_stack),
                            "block_index": block_index,
                            "paragraph_index": paragraph_index,
                            "style": self._style_name(block),
                        },
                    )
                )
                continue

            table_index += 1
            table_text, row_count, column_count = self._table_text(block)
            if not table_text:
                continue

            elements.append(
                ParsedElement(
                    text=table_text,
                    element_type="table",
                    source_metadata={
                        "heading_path": list(heading_stack),
                        "block_index": block_index,
                        "table_index": table_index,
                        "row_count": row_count,
                        "column_count": column_count,
                    },
                )
            )

        core_title = self._clean_text(document.core_properties.title or "")
        title = document_title or core_title or Path(parse_input.filename).stem

        return ParsedDocument(
            title=title,
            file_type="docx",
            file_size=parse_input.file_size,
            elements=tuple(elements),
            metadata={
                "content_type": parse_input.content_type,
                "paragraph_count": paragraph_index,
                "table_count": table_index,
                "embedded_image_count": len(document.inline_shapes),
                "embedded_images_interpreted": False,
            },
        )

    @staticmethod
    def _iter_blocks(document: WordDocument):
        """Yield paragraphs and tables in their source-document order."""
        for child in document.element.body.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, document)
            elif isinstance(child, CT_Tbl):
                yield Table(child, document)

    @staticmethod
    def _clean_text(value: str) -> str:
        lines = [" ".join(line.split()) for line in value.splitlines()]
        return "\n".join(line for line in lines if line).strip()

    @classmethod
    def _heading_level(cls, paragraph: Paragraph) -> int | None:
        style = paragraph.style
        if style is None:
            return None

        for candidate in (style.style_id or "", style.name or ""):
            match = _HEADING_PATTERN.match(candidate.strip())
            if match:
                return int(match.group(1))
        return None

    @staticmethod
    def _is_title(paragraph: Paragraph) -> bool:
        style = paragraph.style
        if style is None:
            return False
        return (style.style_id or "").lower() == "title" or (
            style.name or ""
        ).lower() == "title"

    @staticmethod
    def _style_name(paragraph: Paragraph) -> str | None:
        style = paragraph.style
        return style.name if style is not None else None

    @classmethod
    def _table_text(cls, table: Table) -> tuple[str, int, int]:
        rows: list[str] = []
        max_columns = 0

        for row_number, row in enumerate(table.rows, start=1):
            cells = [cls._clean_text(cell.text) for cell in row.cells]
            max_columns = max(max_columns, len(cells))
            if not any(cells):
                continue
            rows.append(
                f"Row {row_number}: "
                + " | ".join(cell or "(empty)" for cell in cells)
            )

        if not rows:
            return "", len(table.rows), max_columns

        return "Table:\n" + "\n".join(rows), len(table.rows), max_columns
