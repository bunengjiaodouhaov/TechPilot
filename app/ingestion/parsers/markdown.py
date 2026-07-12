from pathlib import Path

from app.ingestion.parsers.base import BaseParser
from app.ingestion.schemas import ParseInput, ParsedDocument, ParsedElement


class MarkdownParser(BaseParser):
    """Parse Markdown while preserving heading paths and source line ranges."""

    def parse(self, parse_input: ParseInput) -> ParsedDocument:
        try:
            content = parse_input.file_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Markdown file must be valid UTF-8.") from exc

        elements: list[ParsedElement] = []
        heading_stack: list[str] = []
        paragraph_lines: list[str] = []
        paragraph_start_line: int | None = None

        def flush_paragraph(end_line: int) -> None:
            nonlocal paragraph_lines, paragraph_start_line
            text = "\n".join(paragraph_lines).strip()
            if text and paragraph_start_line is not None:
                elements.append(
                    ParsedElement(
                        text=text,
                        element_type="paragraph",
                        source_metadata={
                            "heading_path": list(heading_stack),
                            "line_start": paragraph_start_line,
                            "line_end": end_line,
                        },
                    )
                )
            paragraph_lines = []
            paragraph_start_line = None

        lines = content.splitlines()
        for line_number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                marker_count = len(stripped) - len(stripped.lstrip("#"))
                is_heading = marker_count <= 6 and len(stripped) > marker_count and stripped[marker_count] == " "
                if is_heading:
                    flush_paragraph(line_number - 1)
                    heading_text = stripped[marker_count + 1 :].strip()
                    heading_stack[:] = heading_stack[: marker_count - 1]
                    heading_stack.append(heading_text)
                    elements.append(
                        ParsedElement(
                            text=heading_text,
                            element_type="heading",
                            source_metadata={
                                "heading_path": list(heading_stack),
                                "heading_level": marker_count,
                                "line_start": line_number,
                                "line_end": line_number,
                            },
                        )
                    )
                    continue

            if not stripped:
                flush_paragraph(line_number - 1)
                continue

            if paragraph_start_line is None:
                paragraph_start_line = line_number
            paragraph_lines.append(line)

        flush_paragraph(len(lines))

        return ParsedDocument(
            title=self._document_title(parse_input.filename, elements),
            file_type="markdown",
            file_size=parse_input.file_size,
            elements=tuple(elements),
            metadata={"content_type": parse_input.content_type},
        )

    @staticmethod
    def _document_title(filename: str, elements: list[ParsedElement]) -> str:
        for element in elements:
            if element.element_type == "heading" and element.source_metadata.get("heading_level") == 1:
                return element.text
        return Path(filename).stem
