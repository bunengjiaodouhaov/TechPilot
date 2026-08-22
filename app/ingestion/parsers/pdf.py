from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from app.ingestion.ocr import PDFOCRProvider
from app.ingestion.parsers.base import BaseParser
from app.ingestion.schemas import ParseInput, ParsedDocument, ParsedElement


class PDFParser(BaseParser):
    """Extract page text, falling back to OCR for low/empty-text pages."""

    def __init__(
        self,
        *,
        ocr_provider: PDFOCRProvider | None = None,
        native_text_min_chars: int = 24,
    ) -> None:
        if native_text_min_chars < 0:
            raise ValueError("native_text_min_chars must be non-negative")
        self._ocr_provider = ocr_provider
        self._native_text_min_chars = native_text_min_chars

    def parse(self, parse_input: ParseInput) -> ParsedDocument:
        try:
            reader = PdfReader(BytesIO(parse_input.file_bytes))
        except Exception as exc:
            raise ValueError("Unable to read PDF file.") from exc

        if reader.is_encrypted:
            raise ValueError("Encrypted PDF files are not supported.")

        elements: list[ParsedElement] = []
        failed_pages: list[int] = []
        ocr_pages: list[int] = []
        native_pages: list[int] = []

        for page_number, page in enumerate(reader.pages, start=1):
            native_text = ""
            try:
                native_text = (page.extract_text() or "").strip()
            except Exception:
                native_text = ""

            if self._has_usable_native_text(native_text):
                elements.append(
                    self._page_element(
                        text=native_text,
                        page_number=page_number,
                        extraction_method="native",
                    )
                )
                native_pages.append(page_number)
                continue

            if self._ocr_provider is None:
                failed_pages.append(page_number)
                continue

            try:
                ocr_text = self._ocr_provider.extract_page_text(
                    pdf_bytes=parse_input.file_bytes,
                    page_index=page_number - 1,
                ).strip()
            except Exception:
                failed_pages.append(page_number)
                continue

            if not ocr_text:
                failed_pages.append(page_number)
                continue

            elements.append(
                self._page_element(
                    text=ocr_text,
                    page_number=page_number,
                    extraction_method="ocr",
                )
            )
            ocr_pages.append(page_number)

        metadata: dict[str, object] = {
            "content_type": parse_input.content_type,
            "page_count": len(reader.pages),
            "native_pages": native_pages,
            "ocr_pages": ocr_pages,
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

    def _has_usable_native_text(self, text: str) -> bool:
        if not text:
            return False
        alnum_count = sum(char.isalnum() for char in text)
        return alnum_count >= self._native_text_min_chars

    @staticmethod
    def _page_element(
        *,
        text: str,
        page_number: int,
        extraction_method: str,
    ) -> ParsedElement:
        return ParsedElement(
            text=text,
            element_type="page_text",
            source_metadata={
                "page_start": page_number,
                "page_end": page_number,
                "extraction_method": extraction_method,
            },
        )

    @staticmethod
    def _document_title(filename: str, reader: PdfReader) -> str:
        metadata_title = reader.metadata.title if reader.metadata else None
        return metadata_title.strip() if metadata_title else Path(filename).stem
