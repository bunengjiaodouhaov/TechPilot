from __future__ import annotations

import os
from pathlib import Path

DEFAULT_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "cache",
        "generated",
    }
)

DEFAULT_MAX_FILE_BYTES = 1024 * 1024
BINARY_SAMPLE_BYTES = 8192

SAFE_ENV_TEMPLATE_NAMES = frozenset(
    {
        ".env.example",
        ".env.sample",
    }
)


class RepositoryReadError(ValueError):
    """Base error for repository read-boundary violations."""


class RepositoryPathError(RepositoryReadError):
    """Raised when a repository path is invalid or unsafe."""


class RepositoryFileRejectedError(RepositoryReadError):
    """Raised when a file is not eligible for repository reading."""


class RepositoryReadBoundary:
    """Define the safe, deterministic read boundary for one repository."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        excluded_dirs: frozenset[str] = DEFAULT_EXCLUDED_DIRS,
    ) -> None:
        if max_file_bytes <= 0:
            raise ValueError("max_file_bytes must be greater than zero")

        resolved_root = Path(root).expanduser().resolve(strict=True)

        if not resolved_root.is_dir():
            raise ValueError("repository root must be a directory")

        self._root = resolved_root
        self._max_file_bytes = max_file_bytes
        self._excluded_dirs = excluded_dirs

    @property
    def root(self) -> Path:
        return self._root

    def normalize_relative_path(self, path: str | Path) -> str:
        raw_path = Path(path)

        if raw_path.is_absolute():
            raise RepositoryPathError("absolute paths are not allowed")

        self._reject_symlink_components(raw_path)

        try:
            resolved = (self._root / raw_path).resolve(strict=True)
        except FileNotFoundError as exc:
            raise RepositoryPathError("repository path does not exist") from exc

        try:
            relative = resolved.relative_to(self._root)
        except ValueError as exc:
            raise RepositoryPathError(
                "repository path escapes repository root"
            ) from exc

        if self._is_excluded(relative):
            raise RepositoryPathError("repository path is excluded")

        return relative.as_posix()

    def resolve_file(self, path: str | Path) -> Path:
        normalized = self.normalize_relative_path(path)
        candidate = self._root / normalized

        if not candidate.is_file():
            raise RepositoryFileRejectedError(
                "repository path is not a regular file"
            )

        try:
            size = candidate.stat().st_size
        except OSError as exc:
            raise RepositoryFileRejectedError(
                "unable to inspect repository file"
            ) from exc

        if size > self._max_file_bytes:
            raise RepositoryFileRejectedError(
                "repository file exceeds maximum size"
            )

        if self._looks_binary(candidate):
            raise RepositoryFileRejectedError(
                "binary repository file is not readable"
            )

        return candidate

    def list_files(self) -> list[str]:
        readable_files: list[str] = []

        for dirpath, dirnames, filenames in os.walk(
            self._root,
            topdown=True,
            followlinks=False,
        ):
            current_dir = Path(dirpath)

            dirnames[:] = [
                dirname
                for dirname in sorted(dirnames)
                if dirname not in self._excluded_dirs
                and not (current_dir / dirname).is_symlink()
            ]

            for filename in sorted(filenames):
                candidate = current_dir / filename

                if candidate.is_symlink():
                    continue

                relative = candidate.relative_to(self._root)

                if self._is_excluded(relative):
                    continue

                try:
                    self.resolve_file(relative)
                except RepositoryReadError:
                    continue

                readable_files.append(relative.as_posix())

        return sorted(readable_files)

    def _reject_symlink_components(self, path: Path) -> None:
        current = self._root

        for part in path.parts:
            current = current / part

            if current.is_symlink():
                raise RepositoryPathError(
                    "symlink repository paths are not allowed"
                )

    def _is_excluded(self, path: Path) -> bool:
        if any(
            part in self._excluded_dirs
            for part in path.parts
        ):
            return True

        return self._is_sensitive_file(path)

    @staticmethod
    def _is_sensitive_file(path: Path) -> bool:
        name = path.name

        if name in SAFE_ENV_TEMPLATE_NAMES:
            return False

        return name == ".env" or name.startswith(".env.")

    @staticmethod
    def _looks_binary(path: Path) -> bool:
        try:
            with path.open("rb") as file:
                sample = file.read(BINARY_SAMPLE_BYTES)
        except OSError as exc:
            raise RepositoryFileRejectedError(
                "unable to read repository file"
            ) from exc

        if b"\x00" in sample:
            return True

        try:
            sample.decode("utf-8")
        except UnicodeDecodeError:
            return True

        return False
