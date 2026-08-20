from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.repository.read_boundary import RepositoryReadBoundary


class CodeEvidenceError(ValueError):
    """Raised when code evidence cannot be built safely."""


class CodeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str
    file_path: str
    symbol: str | None = None
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    snippet: str


class CodeEvidenceBuilder:
    """Build code evidence from authoritative repository text."""

    def __init__(
        self,
        *,
        repository: str,
        boundary: RepositoryReadBoundary,
    ) -> None:
        normalized_repository = repository.strip()

        if not normalized_repository:
            raise ValueError("repository must not be empty")

        self._repository = normalized_repository
        self._boundary = boundary

    def build(
        self,
        *,
        file_path: str,
        line_start: int,
        line_end: int,
        symbol: str | None = None,
    ) -> CodeEvidence:
        if line_start < 1:
            raise CodeEvidenceError(
                "line_start must be greater than zero"
            )

        if line_end < line_start:
            raise CodeEvidenceError(
                "line_end must be greater than or equal to line_start"
            )

        resolved = self._boundary.resolve_file(file_path)
        normalized_path = resolved.relative_to(
            self._boundary.root
        ).as_posix()

        lines = resolved.read_text(
            encoding="utf-8"
        ).splitlines()

        if line_end > len(lines):
            raise CodeEvidenceError(
                "line range exceeds file length"
            )

        snippet = "\n".join(
            lines[line_start - 1 : line_end]
        )

        return CodeEvidence(
            repository=self._repository,
            file_path=normalized_path,
            symbol=symbol,
            line_start=line_start,
            line_end=line_end,
            snippet=snippet,
        )
