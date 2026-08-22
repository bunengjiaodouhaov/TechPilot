from __future__ import annotations

from typing import Protocol


class PDFOCRProvider(Protocol):
    """Provider-neutral OCR interface for one PDF page."""

    def extract_page_text(
        self,
        *,
        pdf_bytes: bytes,
        page_index: int,
    ) -> str:
        """Return OCR text for a zero-based PDF page index."""
        ...


class TesseractPDFOCRProvider:
    """Render one PDF page and OCR it with local Tesseract."""

    def __init__(
        self,
        *,
        dpi: int = 200,
        language: str = "eng",
        psm: int = 3,
    ) -> None:
        if dpi <= 0:
            raise ValueError("dpi must be greater than zero")
        if not language.strip():
            raise ValueError("language must not be empty")
        if psm <= 0:
            raise ValueError("psm must be greater than zero")

        self._dpi = dpi
        self._language = language.strip()
        self._psm = psm

    def extract_page_text(
        self,
        *,
        pdf_bytes: bytes,
        page_index: int,
    ) -> str:
        if not pdf_bytes:
            raise ValueError("pdf_bytes must not be empty")
        if page_index < 0:
            raise ValueError("page_index must be non-negative")

        try:
            import pypdfium2 as pdfium
            import pytesseract
        except ImportError as exc:
            raise RuntimeError(
                "OCR dependencies are missing. Install pypdfium2 and pytesseract."
            ) from exc

        try:
            document = pdfium.PdfDocument(pdf_bytes)
            page = document[page_index]
            bitmap = page.render(scale=self._dpi / 72.0)
            image = bitmap.to_pil()
            text = pytesseract.image_to_string(
                image,
                lang=self._language,
                config=f"--psm {self._psm}",
            )
            return text.strip()
        except Exception as exc:
            raise RuntimeError(
                f"OCR failed for PDF page index {page_index}"
            ) from exc
