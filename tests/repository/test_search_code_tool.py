from pathlib import Path

import pytest

from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolRuntime
from app.repository.read_boundary import RepositoryReadBoundary
from app.repository.tools import SearchCodeTool


def make_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()

    (repository / "b.py").write_text(
        "class OtherService:\n"
        "    pass\n"
        "# EvidenceVerifier\n",
        encoding="utf-8",
    )
    (repository / "a.py").write_text(
        "class EvidenceVerifier:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (repository / ".env").write_text(
        "EvidenceVerifier=secret\n",
        encoding="utf-8",
    )

    return repository


@pytest.mark.asyncio
async def test_search_code_returns_deterministic_matches(
    tmp_path: Path,
) -> None:
    boundary = RepositoryReadBoundary(make_repository(tmp_path))
    tool = SearchCodeTool(boundary)

    output = await tool.execute(
        tool.input_schema(query="EvidenceVerifier")
    )

    assert [
        (match.path, match.line_number)
        for match in output.matches
    ] == [
        ("a.py", 1),
        ("b.py", 3),
    ]
    assert output.match_count == 2
    assert output.truncated is False


@pytest.mark.asyncio
async def test_search_code_is_case_insensitive_by_default(
    tmp_path: Path,
) -> None:
    boundary = RepositoryReadBoundary(make_repository(tmp_path))
    tool = SearchCodeTool(boundary)

    output = await tool.execute(
        tool.input_schema(query="evidenceverifier")
    )

    assert output.match_count == 2


@pytest.mark.asyncio
async def test_search_code_respects_limit(
    tmp_path: Path,
) -> None:
    boundary = RepositoryReadBoundary(make_repository(tmp_path))
    tool = SearchCodeTool(boundary)

    output = await tool.execute(
        tool.input_schema(
            query="EvidenceVerifier",
            limit=1,
        )
    )

    assert output.match_count == 1
    assert output.truncated is True


@pytest.mark.asyncio
async def test_search_code_runs_through_harness(
    tmp_path: Path,
) -> None:
    boundary = RepositoryReadBoundary(make_repository(tmp_path))

    registry = ToolRegistry()
    registry.register(SearchCodeTool(boundary))

    result = await ToolRuntime().invoke(
        tool=registry.get("search_code"),
        arguments={"query": "EvidenceVerifier"},
    )

    assert result.ok is True
    assert result.data is not None
    assert result.data["match_count"] == 2


@pytest.mark.asyncio
async def test_runtime_propagates_search_code_truncation(
    tmp_path: Path,
) -> None:
    boundary = RepositoryReadBoundary(make_repository(tmp_path))
    tool = SearchCodeTool(boundary)

    result = await ToolRuntime().invoke(
        tool=tool,
        arguments={"query": "EvidenceVerifier", "limit": 1},
    )

    assert result.ok is True
    assert result.truncated is True

