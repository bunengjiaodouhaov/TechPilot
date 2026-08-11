from pathlib import Path

import pytest

from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolRuntime
from app.repository.read_boundary import RepositoryReadBoundary
from app.repository.tools import SearchSymbolTool


def make_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()

    (repository / "service.py").write_text(
        "# UserService in a comment must not become a symbol\n"
        "TEXT = 'UserService'\n"
        "\n"
        "class UserService:\n"
        "    async def load_user(self):\n"
        "        pass\n"
        "\n"
        "def build_user():\n"
        "    pass\n",
        encoding="utf-8",
    )

    return repository


@pytest.mark.asyncio
async def test_search_symbol_finds_real_code_definitions(
    tmp_path: Path,
) -> None:
    boundary = RepositoryReadBoundary(make_repository(tmp_path))
    tool = SearchSymbolTool(boundary)

    output = await tool.execute(
        tool.input_schema(query="UserService")
    )

    assert output.match_count == 1
    assert output.matches[0].kind == "class"
    assert output.matches[0].qualified_name == "UserService"
    assert output.matches[0].line_start == 4


@pytest.mark.asyncio
async def test_search_symbol_finds_class_methods(
    tmp_path: Path,
) -> None:
    boundary = RepositoryReadBoundary(make_repository(tmp_path))
    tool = SearchSymbolTool(boundary)

    output = await tool.execute(
        tool.input_schema(query="load_user")
    )

    assert output.match_count == 1
    assert output.matches[0].kind == "method"
    assert output.matches[0].qualified_name == "UserService.load_user"


@pytest.mark.asyncio
async def test_parse_error_does_not_break_repository_search(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)

    (repository / "broken.py").write_text(
        "def broken(:\n",
        encoding="utf-8",
    )

    tool = SearchSymbolTool(
        RepositoryReadBoundary(repository)
    )

    output = await tool.execute(
        tool.input_schema(query="UserService")
    )

    assert output.match_count == 1
    assert output.parse_error_count == 1


@pytest.mark.asyncio
async def test_search_symbol_runs_through_harness(
    tmp_path: Path,
) -> None:
    boundary = RepositoryReadBoundary(make_repository(tmp_path))

    registry = ToolRegistry()
    registry.register(SearchSymbolTool(boundary))

    result = await ToolRuntime().invoke(
        tool=registry.get("search_symbol"),
        arguments={"query": "build_user"},
    )

    assert result.ok is True
    assert result.data is not None
    assert result.data["match_count"] == 1
    assert result.data["matches"][0]["kind"] == "function"


@pytest.mark.asyncio
async def test_search_symbol_accepts_exact_qualified_name(
    tmp_path: Path,
) -> None:
    boundary = RepositoryReadBoundary(make_repository(tmp_path))
    tool = SearchSymbolTool(boundary)

    output = await tool.execute(
        tool.input_schema(query="UserService.load_user")
    )

    assert output.match_count == 1
    assert output.matches[0].kind == "method"
    assert output.matches[0].qualified_name == "UserService.load_user"
