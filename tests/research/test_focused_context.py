from __future__ import annotations

from app.research.focused_context import (
    query_terms,
    select_query_focused_window,
)


def test_query_terms_split_camel_case_and_keep_timeout() -> None:
    terms = query_terms(
        "How does ToolRuntime enforce timeout handling?"
    )

    assert "tool" in terms
    assert "runtime" in terms
    assert "timeout" in terms


def test_focused_window_keeps_relevant_later_context() -> None:
    snippet = (
        "A" * 1800
        + " asyncio.wait_for(tool.execute(), timeout=tool.timeout_seconds) "
        + "B" * 300
        + " ToolErrorCode.TIMEOUT "
        + "C" * 3000
    )

    visible, start, end = select_query_focused_window(
        snippet=snippet,
        query="How does ToolRuntime enforce timeout handling?",
        budget=2200,
    )

    assert len(visible) <= 2200
    assert start > 0
    assert end > start
    assert "timeout_seconds" in visible
    assert "ToolErrorCode.TIMEOUT" in visible


def test_focused_window_falls_back_to_prefix_without_match() -> None:
    snippet = "abcdefghij" * 500

    visible, start, end = select_query_focused_window(
        snippet=snippet,
        query="unrelated semantic query",
        budget=100,
    )

    assert visible == snippet[:100]
    assert start == 0
    assert end == 100
