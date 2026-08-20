from __future__ import annotations

import pytest

from app.harness.evidence_pack import EvidenceIssueKind
from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolRiskLevel, ToolRuntime
from app.repository.repo_explorer import RepoExploreRequest, RepoExplorer
from app.repository.tools import (
    ReadFileInput,
    ReadFileOutput,
    SearchCodeInput,
    SearchCodeOutput,
)


class KnownPathReadTool:
    name = "read_file"
    description = "fake authoritative known-path read"
    input_schema = ReadFileInput
    output_schema = ReadFileOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 0.1
    max_retries = 0

    def __init__(
        self,
        *,
        returned_path: str = "docs/PROJECT_STATUS.md",
    ) -> None:
        self.returned_path = returned_path
        self.calls: list[str] = []

    async def execute(
        self,
        tool_input: ReadFileInput,
    ) -> ReadFileOutput:
        self.calls.append(tool_input.path)
        content = "# Project Status\n\nP3: closed\nP4: active\n"
        return ReadFileOutput(
            path=self.returned_path,
            content=content,
            size_bytes=len(content.encode("utf-8")),
        )


class ForbiddenSearchTool:
    name = "search_code"
    description = "must never be called in path mode"
    input_schema = SearchCodeInput
    output_schema = SearchCodeOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 0.1
    max_retries = 0

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, tool_input: SearchCodeInput) -> SearchCodeOutput:
        self.calls += 1
        raise AssertionError("path mode must not call search_code")


def make_explorer(*tools: object) -> RepoExplorer:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return RepoExplorer(
        repository="TechPilot",
        registry=registry,
        runtime=ToolRuntime(),
    )


@pytest.mark.asyncio
async def test_path_mode_materializes_known_path_without_search() -> None:
    read_tool = KnownPathReadTool()
    search_tool = ForbiddenSearchTool()
    explorer = make_explorer(read_tool, search_tool)

    pack = await explorer.explore(
        RepoExploreRequest(
            query="docs/PROJECT_STATUS.md",
            task_intent="inspect the explicitly named status document",
            search_mode="path",
        )
    )

    assert read_tool.calls == ["docs/PROJECT_STATUS.md"]
    assert search_tool.calls == 0
    assert pack.provenance_integrity is True
    assert pack.incomplete is False
    assert pack.issues == []
    assert len(pack.evidence) == 1

    evidence = pack.evidence[0]
    assert evidence.file_path == "docs/PROJECT_STATUS.md"
    assert evidence.symbol is None
    assert evidence.line_start == 1
    assert "P4: active" in evidence.snippet


@pytest.mark.asyncio
async def test_path_mode_does_not_fallback_when_read_file_is_unavailable() -> None:
    search_tool = ForbiddenSearchTool()
    explorer = make_explorer(search_tool)

    pack = await explorer.explore(
        RepoExploreRequest(
            query="docs/PROJECT_STATUS.md",
            task_intent="inspect the explicitly named status document",
            search_mode="path",
        )
    )

    assert search_tool.calls == 0
    assert pack.evidence == []
    assert pack.incomplete is True
    assert pack.provenance_integrity is True
    assert len(pack.issues) == 1
    assert pack.issues[0].kind is EvidenceIssueKind.TOOL_UNAVAILABLE
    assert pack.issues[0].tool_name == "read_file"


@pytest.mark.asyncio
async def test_path_mode_rejects_authoritative_path_mismatch() -> None:
    read_tool = KnownPathReadTool(
        returned_path="docs/OTHER_STATUS.md",
    )
    explorer = make_explorer(read_tool)

    pack = await explorer.explore(
        RepoExploreRequest(
            query="docs/PROJECT_STATUS.md",
            task_intent="inspect the explicitly named status document",
            search_mode="path",
        )
    )

    assert pack.evidence == []
    assert pack.provenance_integrity is False
    assert pack.incomplete is True
    assert any(
        issue.kind is EvidenceIssueKind.PROVENANCE_MISMATCH
        and issue.file_path == "docs/PROJECT_STATUS.md"
        for issue in pack.issues
    )
