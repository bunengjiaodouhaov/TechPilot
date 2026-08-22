from __future__ import annotations

import pytest

from scripts.document_benchmark_ingest import split_text_with_overlap


def test_no_overlap_preserves_bounded_nonempty_pieces() -> None:
    text = " ".join(f"token{i}" for i in range(100))
    pieces = split_text_with_overlap(
        text,
        max_chars=120,
        overlap_chars=0,
    )
    assert len(pieces) > 1
    assert all(piece.strip() for piece in pieces)
    assert all(len(piece) <= 120 for piece in pieces)


def test_overlap_repeats_boundary_context() -> None:
    text = " ".join(f"word{i}" for i in range(80))
    pieces = split_text_with_overlap(
        text,
        max_chars=120,
        overlap_chars=30,
    )
    assert len(pieces) > 1
    first_words = set(pieces[0].split())
    second_words = set(pieces[1].split())
    assert first_words & second_words


def test_invalid_overlap_rejected() -> None:
    with pytest.raises(ValueError):
        split_text_with_overlap(
            "hello world",
            max_chars=100,
            overlap_chars=100,
        )

