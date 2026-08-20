from __future__ import annotations

from pathlib import Path

import pytest

from app.repository.read_boundary import (
    BINARY_SAMPLE_BYTES,
    RepositoryFileRejectedError,
    RepositoryReadBoundary,
)


def test_utf8_character_split_at_binary_sample_boundary_is_text(
    tmp_path: Path,
) -> None:
    prefix = b"a" * (BINARY_SAMPLE_BYTES - 1)
    content = prefix + "你".encode("utf-8") + b"\nrest\n"

    path = tmp_path / "status.md"
    path.write_bytes(content)

    boundary = RepositoryReadBoundary(tmp_path)

    resolved = boundary.resolve_file("status.md")

    assert resolved == path
    assert resolved.read_text(encoding="utf-8").endswith("你\nrest\n")


def test_invalid_utf8_inside_binary_sample_is_still_rejected(
    tmp_path: Path,
) -> None:
    content = b"valid-prefix\n" + b"\xff" + b"\nrest\n"

    path = tmp_path / "bad.txt"
    path.write_bytes(content)

    boundary = RepositoryReadBoundary(tmp_path)

    with pytest.raises(
        RepositoryFileRejectedError,
        match="binary repository file is not readable",
    ):
        boundary.resolve_file("bad.txt")


def test_nul_byte_is_still_rejected_as_binary(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nul.txt"
    path.write_bytes(b"text\x00more")

    boundary = RepositoryReadBoundary(tmp_path)

    with pytest.raises(
        RepositoryFileRejectedError,
        match="binary repository file is not readable",
    ):
        boundary.resolve_file("nul.txt")
