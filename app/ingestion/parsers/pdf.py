from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from app.ingestion.parsers.base import BaseParser
from app.ingestion.schemas import ParseInput, ParsedDocument, ParsedElement


class PDFParser(BaseParser):
    """Extract page-level text from PDF files while preserving page numbers."""

    def parse(self, parse_input: ParseInput) -> ParsedDocument:
        try:
            reader = PdfReader(BytesIO(parse_input.file_bytes))
        except Exception as exc:  # pypdf raises several format-specific exceptions
            raise ValueError("Unable to read PDF file.") from exc

        if reader.is_encrypted:
            raise ValueError("Encrypted PDF files are not supported.")

        elements: list[ParsedElement] = []
        failed_pages: list[int] = []

        for page_number, page in enumerate(reader.pages, start=1):
            try:
                text = (page.extract_text() or "").strip()
            except Exception:
                failed_pages.append(page_number)
                continue

            if text:
                elements.append(
                    ParsedElement(
                        text=text,
                        element_type="page_text",
                        source_metadata={
                            "page_start": page_number,
                            "page_end": page_number,
                        },
                    )
                )

        metadata: dict[str, object] = {
            "content_type": parse_input.content_type,
            "page_count": len(reader.pages),
        }
        if failed_pages:
            metadata["failed_pages"] = failed_pages

        return ParsedDocument(
            title=self._document_title(parse_input.filename, reader),
            file_type="pdf",
            file_size=parse_input.file_size,
            elements=tuple(elements),
            metadata=metadata,
        )

    @staticmethod
    def _document_title(filename: str, reader: PdfReader) -> str:
        metadata_title = reader.metadata.title if reader.metadata else None
        return metadata_title.strip() if metadata_title else Path(filename).stem
