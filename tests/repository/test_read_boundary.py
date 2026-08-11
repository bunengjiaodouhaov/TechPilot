from pathlib import Path

import pytest

from app.repository.read_boundary import (
    RepositoryFileRejectedError,
    RepositoryPathError,
    RepositoryReadBoundary,
)


def make_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    return repository


def test_normalizes_paths_relative_to_repository_root(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    app_dir = repository / "app"
    app_dir.mkdir()
    target = app_dir / "main.py"
    target.write_text("print('ok')\n", encoding="utf-8")

    boundary = RepositoryReadBoundary(repository)

    assert boundary.root == repository.resolve()
    assert (
        boundary.normalize_relative_path(
            "app/../app/main.py"
        )
        == "app/main.py"
    )
    assert boundary.resolve_file("app/main.py") == target


def test_rejects_absolute_and_escaping_paths(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    boundary = RepositoryReadBoundary(repository)

    with pytest.raises(
        RepositoryPathError,
        match="absolute",
    ):
        boundary.normalize_relative_path(outside)

    with pytest.raises(
        RepositoryPathError,
        match="escapes",
    ):
        boundary.normalize_relative_path("../outside.txt")


def test_rejects_symlink_paths(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    symlink = repository / "escape.txt"
    symlink.symlink_to(outside)

    boundary = RepositoryReadBoundary(repository)

    with pytest.raises(
        RepositoryPathError,
        match="symlink",
    ):
        boundary.normalize_relative_path("escape.txt")

    assert boundary.list_files() == []


def test_excludes_repository_internal_directories(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)

    excluded_dirs = (
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "cache",
        "generated",
    )

    for dirname in excluded_dirs:
        directory = repository / dirname
        directory.mkdir()
        (directory / "hidden.py").write_text(
            "hidden = True\n",
            encoding="utf-8",
        )

    app_dir = repository / "app"
    app_dir.mkdir()
    (app_dir / "visible.py").write_text(
        "visible = True\n",
        encoding="utf-8",
    )

    boundary = RepositoryReadBoundary(repository)

    assert boundary.list_files() == ["app/visible.py"]

    with pytest.raises(
        RepositoryPathError,
        match="excluded",
    ):
        boundary.resolve_file(".git/hidden.py")


def test_rejects_binary_and_oversized_files(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)

    (repository / "binary.bin").write_bytes(
        b"\x00\x01\x02\x03"
    )
    (repository / "large.txt").write_text(
        "123456789",
        encoding="utf-8",
    )
    (repository / "small.txt").write_text(
        "12345678",
        encoding="utf-8",
    )

    boundary = RepositoryReadBoundary(
        repository,
        max_file_bytes=8,
    )

    with pytest.raises(
        RepositoryFileRejectedError,
        match="binary",
    ):
        boundary.resolve_file("binary.bin")

    with pytest.raises(
        RepositoryFileRejectedError,
        match="maximum size",
    ):
        boundary.resolve_file("large.txt")

    assert boundary.list_files() == ["small.txt"]


def test_repository_traversal_is_deterministic(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)

    package = repository / "pkg"
    package.mkdir()

    for path in (
        repository / "z.py",
        package / "b.py",
        repository / "a.py",
        package / "a.py",
    ):
        path.write_text(
            f"# {path.name}\n",
            encoding="utf-8",
        )

    boundary = RepositoryReadBoundary(repository)

    first = boundary.list_files()
    second = boundary.list_files()

    assert first == second
    assert first == [
        "a.py",
        "pkg/a.py",
        "pkg/b.py",
        "z.py",
    ]


def test_excludes_sensitive_env_files(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)

    (repository / ".env").write_text(
        "API_KEY=secret\n",
        encoding="utf-8",
    )
    (repository / ".env.local").write_text(
        "API_KEY=local-secret\n",
        encoding="utf-8",
    )
    (repository / ".env.example").write_text(
        "API_KEY=<placeholder>\n",
        encoding="utf-8",
    )

    boundary = RepositoryReadBoundary(repository)

    assert boundary.list_files() == [".env.example"]

    with pytest.raises(
        RepositoryPathError,
        match="excluded",
    ):
        boundary.resolve_file(".env")
