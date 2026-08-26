from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.ingestion.ocr import TesseractPDFOCRProvider
from app.ingestion.parsers import (
    BaseParser,
    DOCXParser,
    MarkdownParser,
    PDFParser,
)


class UnsupportedFileTypeError(ValueError):
    """Raised when no parser supports the uploaded file."""


class FileTypeConflictError(ValueError):
    """Raised when filename and MIME type identify different formats."""


@dataclass(frozen=True, slots=True)
class ParserSelection:
    file_type: str
    parser: BaseParser


class ParserRouter:
    """Choose a parser using both filename and declared MIME type."""

    _EXTENSION_TYPES = {
        ".md": "markdown",
        ".markdown": "markdown",
        ".pdf": "pdf",
        ".docx": "docx",
    }

    _CONTENT_TYPES = {
        "text/markdown": "markdown",
        "text/x-markdown": "markdown",
        "application/pdf": "pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    }

    def __init__(self) -> None:
        self._parsers: dict[str, BaseParser] = {
            "markdown": MarkdownParser(),
            "pdf": PDFParser(
                ocr_provider=TesseractPDFOCRProvider(),
                native_text_min_chars=24,
            ),
            "docx": DOCXParser(),
        }

    def select(
        self,
        filename: str,
        content_type: str,
    ) -> ParserSelection:
        extension = Path(filename).suffix.lower()
        extension_type = self._EXTENSION_TYPES.get(extension)

        normalized_content_type = (
            content_type.split(";", maxsplit=1)[0]
            .strip()
            .lower()
        )
        content_type_result = self._CONTENT_TYPES.get(
            normalized_content_type
        )

        if (
            extension_type is not None
            and content_type_result is not None
            and extension_type != content_type_result
        ):
            raise FileTypeConflictError(
                "Filename extension and content type identify "
                "different file formats."
            )

        file_type = extension_type or content_type_result

        if file_type is None:
            raise UnsupportedFileTypeError(
                f"Unsupported file type: filename={filename!r}, "
                f"content_type={content_type!r}"
            )

        return ParserSelection(
            file_type=file_type,
            parser=self._parsers[file_type],
        )
