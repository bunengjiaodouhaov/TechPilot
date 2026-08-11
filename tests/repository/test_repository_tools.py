from pathlib import Path

import pytest

from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolRuntime
from app.repository.read_boundary import RepositoryReadBoundary
from app.repository.tools import ReadFileTool, TreeTool


def make_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()

    app_dir = repository / "app"
    app_dir.mkdir()

    (app_dir / "main.py").write_text(
        "print('hello')\n",
        encoding="utf-8",
    )
    (repository / ".env").write_text(
        "API_KEY=secret\n",
        encoding="utf-8",
    )

    git_dir = repository / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text(
        "hidden\n",
        encoding="utf-8",
    )

    return repository


@pytest.mark.asyncio
async def test_tree_returns_only_readable_repository_files(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    tool = TreeTool(RepositoryReadBoundary(repository))

    output = await tool.execute(tool.input_schema())

    assert output.files == ["app/main.py"]
    assert output.file_count == 1


@pytest.mark.asyncio
async def test_read_file_returns_authoritative_file_content(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    tool = ReadFileTool(RepositoryReadBoundary(repository))

    output = await tool.execute(
        tool.input_schema(path="app/main.py")
    )

    assert output.path == "app/main.py"
    assert output.content == "print('hello')\n"
    assert output.size_bytes > 0


@pytest.mark.asyncio
async def test_repository_tools_work_through_runtime_and_registry(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    boundary = RepositoryReadBoundary(repository)

    registry = ToolRegistry()
    registry.register(TreeTool(boundary))
    registry.register(ReadFileTool(boundary))

    runtime = ToolRuntime()

    tree_result = await runtime.invoke(
        tool=registry.get("tree"),
        arguments={},
    )
    read_result = await runtime.invoke(
        tool=registry.get("read_file"),
        arguments={"path": "app/main.py"},
    )

    assert tree_result.ok is True
    assert tree_result.data == {
        "files": ["app/main.py"],
        "file_count": 1,
    }

    assert read_result.ok is True
    assert read_result.data is not None
    assert read_result.data["path"] == "app/main.py"
    assert read_result.data["content"] == "print('hello')\n"


@pytest.mark.asyncio
async def test_read_file_cannot_bypass_boundary(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    tool = ReadFileTool(RepositoryReadBoundary(repository))
    runtime = ToolRuntime()

    result = await runtime.invoke(
        tool=tool,
        arguments={"path": ".env"},
    )

    assert result.ok is False
