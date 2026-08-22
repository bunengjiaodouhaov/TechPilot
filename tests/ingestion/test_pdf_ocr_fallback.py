from __future__ import annotations

from io import BytesIO

from pypdf import PdfWriter

from app.ingestion.parsers.pdf import PDFParser
from app.ingestion.schemas import ParseInput


class FakeOCRProvider:
    def __init__(self, text: str = "OCR recovered page text") -> None:
        self.text = text
        self.calls: list[int] = []

    def extract_page_text(
        self,
        *,
        pdf_bytes: bytes,
        page_index: int,
    ) -> str:
        self.calls.append(page_index)
        return self.text


class FailingOCRProvider:
    def extract_page_text(
        self,
        *,
        pdf_bytes: bytes,
        page_index: int,
    ) -> str:
        raise RuntimeError("OCR unavailable")


def blank_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def make_input() -> ParseInput:
    payload = blank_pdf_bytes()
    return ParseInput(
        filename="scan.pdf",
        content_type="application/pdf",
        file_size=len(payload),
        file_bytes=payload,
    )


def test_pdf_parser_uses_ocr_for_empty_text_page() -> None:
    provider = FakeOCRProvider("Recovered security requirement")
    parser = PDFParser(
        ocr_provider=provider,
        native_text_min_chars=24,
    )

    result = parser.parse(make_input())

    assert provider.calls == [0]
    assert len(result.elements) == 1
    assert result.elements[0].text == "Recovered security requirement"
    assert result.elements[0].source_metadata["page_start"] == 1
    assert result.elements[0].source_metadata["page_end"] == 1
    assert result.elements[0].source_metadata["extraction_method"] == "ocr"
    assert result.metadata["ocr_pages"] == [1]
    assert result.metadata["native_pages"] == []
    assert "failed_pages" not in result.metadata


def test_pdf_parser_marks_page_failed_when_ocr_fails() -> None:
    parser = PDFParser(
        ocr_provider=FailingOCRProvider(),
        native_text_min_chars=24,
    )

    result = parser.parse(make_input())

    assert result.elements == ()
    assert result.metadata["failed_pages"] == [1]
    assert result.metadata["ocr_pages"] == []
