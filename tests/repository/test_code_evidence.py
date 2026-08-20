from pathlib import Path

import pytest
from pydantic import ValidationError

from app.repository.code_evidence import (
    CodeEvidence,
    CodeEvidenceBuilder,
    CodeEvidenceError,
)
from app.repository.read_boundary import (
    RepositoryPathError,
    RepositoryReadBoundary,
)


def make_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()

    app_dir = repository / "app"
    app_dir.mkdir()

    (app_dir / "service.py").write_text(
        "class UserService:\n"
        "    def load_user(self):\n"
        "        return 'user'\n"
        "\n"
        "VALUE = 1\n",
        encoding="utf-8",
    )

    return repository


def test_builds_evidence_from_authoritative_file_lines(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)

    builder = CodeEvidenceBuilder(
        repository="TechPilot",
        boundary=RepositoryReadBoundary(repository),
    )

    evidence = builder.build(
        file_path="app/service.py",
        line_start=1,
        line_end=3,
        symbol="UserService.load_user",
    )

    assert evidence == CodeEvidence(
        repository="TechPilot",
        file_path="app/service.py",
        symbol="UserService.load_user",
        line_start=1,
        line_end=3,
        snippet=(
            "class UserService:\n"
            "    def load_user(self):\n"
            "        return 'user'"
        ),
    )


def test_rejects_invalid_line_range(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)

    builder = CodeEvidenceBuilder(
        repository="TechPilot",
        boundary=RepositoryReadBoundary(repository),
    )

    with pytest.raises(
        CodeEvidenceError,
        match="line_end",
    ):
        builder.build(
            file_path="app/service.py",
            line_start=3,
            line_end=2,
        )

    with pytest.raises(
        CodeEvidenceError,
        match="file length",
    ):
        builder.build(
            file_path="app/service.py",
            line_start=1,
            line_end=100,
        )


def test_evidence_cannot_bypass_repository_boundary(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)

    (repository / ".env").write_text(
        "SECRET=value\n",
        encoding="utf-8",
    )

    builder = CodeEvidenceBuilder(
        repository="TechPilot",
        boundary=RepositoryReadBoundary(repository),
    )

    with pytest.raises(RepositoryPathError):
        builder.build(
            file_path=".env",
            line_start=1,
            line_end=1,
        )


def test_code_evidence_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        CodeEvidence.model_validate(
            {
                "repository": "TechPilot",
                "file_path": "app/main.py",
                "symbol": None,
                "line_start": 1,
                "line_end": 1,
                "snippet": "content",
                "invented_source": "llm",
            }
        )
