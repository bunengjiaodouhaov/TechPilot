import pytest

from app.harness.tool_runtime import ToolRuntime
from app.repository.module_structure import PythonModuleStructureService
from app.repository.module_structure_tool import InspectModulesTool
from app.repository.read_boundary import RepositoryReadBoundary


@pytest.mark.asyncio
async def test_inspect_modules_tool_exposes_structure_without_source_bodies(
    tmp_path,
) -> None:
    (tmp_path / "repository.py").write_text(
        "class UserRepository:\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "service.py").write_text(
        "from repository import UserRepository\n"
        "class UserService:\n"
        "    pass\n",
        encoding="utf-8",
    )

    tool = InspectModulesTool(
        service=PythonModuleStructureService(
            boundary=RepositoryReadBoundary(tmp_path),
        )
    )
    result = await ToolRuntime().invoke(
        tool=tool,
        arguments={"limit": 10},
    )

    assert result.ok is True
    assert result.truncated is False
    assert result.data is not None
    assert result.data["module_count"] == 2
    service_module = next(
        module
        for module in result.data["modules"]
        if module["module"] == "service"
    )
    assert service_module["internal_dependencies"][0]["module"] == "repository"
    assert service_module["symbols"][0]["name"] == "UserService"
    assert "content" not in service_module


@pytest.mark.asyncio
async def test_inspect_modules_tool_propagates_truncation(tmp_path) -> None:
    (tmp_path / "a.py").write_text("def a():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def b():\n    pass\n", encoding="utf-8")

    result = await ToolRuntime().invoke(
        tool=InspectModulesTool(
            service=PythonModuleStructureService(
                boundary=RepositoryReadBoundary(tmp_path),
            )
        ),
        arguments={"limit": 1},
    )

    assert result.ok is True
    assert result.truncated is True
