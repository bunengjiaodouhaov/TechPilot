import pytest

from app.ingestion.parsers.docx import DOCXParser
from app.ingestion.router import FileTypeConflictError, ParserRouter


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_parser_router_selects_docx_by_extension_and_mime() -> None:
    selection = ParserRouter().select(
        filename="architecture.docx",
        content_type=DOCX_MIME,
    )

    assert selection.file_type == "docx"
    assert isinstance(selection.parser, DOCXParser)


def test_parser_router_rejects_docx_pdf_type_conflict() -> None:
    with pytest.raises(FileTypeConflictError):
        ParserRouter().select(
            filename="architecture.docx",
            content_type="application/pdf",
        )
