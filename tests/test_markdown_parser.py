from app.ingestion.parsers.markdown import MarkdownParser
from app.ingestion.schemas import ParseInput


def test_markdown_parser_preserves_heading_path_and_lines() -> None:
    content = """# TechPilot

Intro paragraph.

## Parsing

Parser keeps source structure.
"""
    parser = MarkdownParser()

    parsed = parser.parse(
        ParseInput(
            filename="guide.md",
            content_type="text/markdown",
            file_size=len(content.encode("utf-8")),
            file_bytes=content.encode("utf-8"),
        )
    )

    assert parsed.title == "TechPilot"
    assert parsed.file_type == "markdown"
    assert len(parsed.elements) == 4

    parsing_paragraph = parsed.elements[-1]
    assert parsing_paragraph.text == "Parser keeps source structure."
    assert parsing_paragraph.source_metadata == {
        "heading_path": ["TechPilot", "Parsing"],
        "line_start": 7,
        "line_end": 7,
    }


def test_markdown_parser_rejects_non_utf8_content() -> None:
    parser = MarkdownParser()

    try:
        parser.parse(
            ParseInput(
                filename="invalid.md",
                content_type="text/markdown",
                file_size=1,
                file_bytes=b"\xff",
            )
        )
    except ValueError as exc:
        assert str(exc) == "Markdown file must be valid UTF-8."
    else:
        raise AssertionError("Expected invalid UTF-8 Markdown to fail.")
