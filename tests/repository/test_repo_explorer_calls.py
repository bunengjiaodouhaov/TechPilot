import pytest

from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolRiskLevel, ToolRuntime
from app.repository.call_relationship_tool import (
    InspectCallsInput,
    InspectCallsOutput,
    StaticCallMatch,
)
from app.repository.repo_explorer import RepoExploreRequest, RepoExplorer
from app.repository.tools import ReadFileInput, ReadFileOutput


class CallTool:
    name = "inspect_calls"
    description = "fake static calls"
    input_schema = InspectCallsInput
    output_schema = InspectCallsOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 0.1
    max_retries = 0

    async def execute(
        self,
        tool_input: InspectCallsInput,
    ) -> InspectCallsOutput:
        return InspectCallsOutput(
            query=tool_input.query,
            matches=[
                StaticCallMatch(
                    path="app/service.py",
                    caller="UserService.load_user",
                    callee="self.repository.get_user",
                    line_start=3,
                    line_end=3,
                )
            ],
            match_count=1,
            python_file_count=1,
            parse_error_count=0,
            read_error_count=0,
            truncated=False,
        )


class ReadTool:
    name = "read_file"
    description = "fake authoritative read"
    input_schema = ReadFileInput
    output_schema = ReadFileOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 0.1
    max_retries = 0

    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        tool_input: ReadFileInput,
    ) -> ReadFileOutput:
        self.calls += 1
        content = (
            "class UserService:\n"
            "    def load_user(self):\n"
            "        return self.repository.get_user()\n"
        )
        return ReadFileOutput(
            path="app/service.py",
            content=content,
            size_bytes=len(content.encode("utf-8")),
        )


@pytest.mark.asyncio
async def test_call_mode_rebuilds_authoritative_callsite_evidence() -> None:
    registry = ToolRegistry()
    registry.register(CallTool())
    read_tool = ReadTool()
    registry.register(read_tool)

    explorer = RepoExplorer(
        repository="TechPilot",
        registry=registry,
        runtime=ToolRuntime(),
    )
    pack = await explorer.explore(
        RepoExploreRequest(
            query="load_user call chain",
            task_intent="understand static call relationships",
            search_mode="call",
            limit=10,
        )
    )

    assert pack.provenance_integrity is True
    assert pack.incomplete is False
    assert len(pack.evidence) == 1
    assert pack.evidence[0].file_path == "app/service.py"
    assert pack.evidence[0].symbol == "UserService.load_user"
    assert pack.evidence[0].line_start == 3
    assert pack.evidence[0].snippet == (
        "        return self.repository.get_user()"
    )
    assert read_tool.calls == 1
