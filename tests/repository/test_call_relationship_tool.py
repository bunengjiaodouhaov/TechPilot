import pytest

from app.harness.tool_runtime import ToolRuntime
from app.repository.call_relationship_tool import InspectCallsTool
from app.repository.call_relationships import PythonCallRelationshipService
from app.repository.read_boundary import RepositoryReadBoundary


@pytest.mark.asyncio
async def test_inspect_calls_runs_through_runtime_and_propagates_truncation(
    tmp_path,
) -> None:
    (tmp_path / "service.py").write_text(
        "def load_user():\n"
        "    first()\n"
        "    second()\n",
        encoding="utf-8",
    )

    result = await ToolRuntime().invoke(
        tool=InspectCallsTool(
            service=PythonCallRelationshipService(
                boundary=RepositoryReadBoundary(tmp_path),
            )
        ),
        arguments={"query": "load_user", "limit": 1},
    )

    assert result.ok is True
    assert result.truncated is True
    assert result.data is not None
    assert result.data["match_count"] == 1
    assert result.data["matches"] == [
        {
            "path": "service.py",
            "caller": "load_user",
            "callee": "first",
            "line_start": 2,
            "line_end": 2,
        }
    ]
    assert "content" not in result.data
