from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

from app.ingestion.parsers.pdf import PDFParser
from app.ingestion.schemas import ParseInput


def _build_single_page_pdf() -> bytes:
    """Create a minimal one-page PDF containing extractable text."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)

    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): font_reference,
                }
            )
        }
    )

    content_stream = DecodedStreamObject()
    content_stream.set_data(
        b"BT /F1 24 Tf 72 720 Td (Hello TechPilot) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(content_stream)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_parser_extracts_page_text_and_source_metadata() -> None:
    file_bytes = _build_single_page_pdf()

    parse_input = ParseInput(
        filename="sample.pdf",
        content_type="application/pdf",
        file_size=len(file_bytes),
        file_bytes=file_bytes,
    )

    result = PDFParser().parse(parse_input)

    assert result.title == "sample"
    assert result.file_type == "pdf"
    assert result.file_size == len(file_bytes)
    assert result.metadata["page_count"] == 1
    assert len(result.elements) == 1

    element = result.elements[0]

    assert element.text == "Hello TechPilot"
    assert element.element_type == "page_text"
    assert element.source_metadata == {
        "page_start": 1,
        "page_end": 1,
    }
