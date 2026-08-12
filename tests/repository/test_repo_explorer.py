import pytest
from app.harness.agent_event import (
    AgentEventType,
    InMemoryAgentEventSink,
)

from app.harness.evidence_pack import EvidenceIssueKind
from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import (
    ToolErrorCode,
    ToolRiskLevel,
    ToolRuntime,
)
from app.repository.ast_service import PythonSymbolKind
from app.repository.repo_explorer import RepoExploreRequest, RepoExplorer
from app.repository.tools import (
    ReadFileInput,
    ReadFileOutput,
    SearchCodeInput,
    SearchCodeMatch,
    SearchCodeOutput,
    SearchSymbolInput,
    SearchSymbolMatch,
    SearchSymbolOutput,
)


class SymbolTool:
    name = "search_symbol"
    description = "fake symbol search"
    input_schema = SearchSymbolInput
    output_schema = SearchSymbolOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 0.1
    max_retries = 0

    async def execute(
        self,
        tool_input: SearchSymbolInput,
    ) -> SearchSymbolOutput:
        return SearchSymbolOutput(
            query=tool_input.query,
            matches=[
                SearchSymbolMatch(
                    path="app/service.py",
                    name="load_user",
                    qualified_name="UserService.load_user",
                    kind=PythonSymbolKind.METHOD,
                    line_start=2,
                    line_end=3,
                )
            ],
            match_count=1,
            parse_error_count=0,
            truncated=False,
        )


class ReadTool:
    name = "read_file"
    description = "fake authoritative file read"
    input_schema = ReadFileInput
    output_schema = ReadFileOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 0.1
    max_retries = 0

    def __init__(self, *, path: str = "app/service.py") -> None:
        self.path = path
        self.calls = 0

    async def execute(
        self,
        tool_input: ReadFileInput,
    ) -> ReadFileOutput:
        self.calls += 1
        content = (
            "class UserService:\n"
            "    def load_user(self):\n"
            "        return 'user'\n"
        )
        return ReadFileOutput(
            path=self.path,
            content=content,
            size_bytes=len(content.encode("utf-8")),
        )


class TruncatedSymbolTool(SymbolTool):
    async def execute(
        self,
        tool_input: SearchSymbolInput,
    ) -> SearchSymbolOutput:
        return SearchSymbolOutput(
            query=tool_input.query,
            matches=[],
            match_count=0,
            parse_error_count=2,
            truncated=True,
        )


class BrokenCodeTool:
    name = "search_code"
    description = "fake broken code search"
    input_schema = SearchCodeInput
    output_schema = SearchCodeOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 0.1
    max_retries = 0

    async def execute(
        self,
        tool_input: SearchCodeInput,
    ) -> SearchCodeOutput:
        raise RuntimeError("boom")


class CodeTool:
    name = "search_code"
    description = "fake code search"
    input_schema = SearchCodeInput
    output_schema = SearchCodeOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 0.1
    max_retries = 0

    async def execute(
        self,
        tool_input: SearchCodeInput,
    ) -> SearchCodeOutput:
        return SearchCodeOutput(
            query=tool_input.query,
            matches=[
                SearchCodeMatch(
                    path="app/service.py",
                    line_number=1,
                    line="class UserService:",
                )
            ],
            match_count=1,
            truncated=False,
        )


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
async def test_builds_evidence_from_read_file_output() -> None:
    read_tool = ReadTool()
    explorer = make_explorer(SymbolTool(), read_tool)

    pack = await explorer.explore(
        RepoExploreRequest(
            query="load_user",
            task_intent="understand user loading",
            search_mode="symbol",
        )
    )

    assert pack.provenance_integrity is True
    assert pack.incomplete is False
    assert len(pack.evidence) == 1
    assert pack.evidence[0].file_path == "app/service.py"
    assert pack.evidence[0].symbol == "UserService.load_user"
    assert pack.evidence[0].snippet == (
        "    def load_user(self):\n"
        "        return 'user'"
    )
    assert read_tool.calls == 1


@pytest.mark.asyncio
async def test_marks_truncation_and_parse_errors_incomplete() -> None:
    explorer = make_explorer(TruncatedSymbolTool())

    pack = await explorer.explore(
        RepoExploreRequest(
            query="missing",
            task_intent="find a symbol",
            search_mode="symbol",
        )
    )

    assert pack.incomplete is True
    assert {issue.kind for issue in pack.issues} == {
        EvidenceIssueKind.TOOL_TRUNCATED,
        EvidenceIssueKind.PARSE_ERROR,
    }


@pytest.mark.asyncio
async def test_records_structured_tool_failure() -> None:
    explorer = make_explorer(BrokenCodeTool())

    pack = await explorer.explore(
        RepoExploreRequest(
            query="broken",
            task_intent="find code",
            search_mode="code",
        )
    )

    assert pack.incomplete is True
    assert len(pack.issues) == 1
    assert pack.issues[0].kind == EvidenceIssueKind.TOOL_FAILURE
    assert pack.issues[0].tool_name == "search_code"
    assert pack.issues[0].error_code == ToolErrorCode.EXECUTION_ERROR


@pytest.mark.asyncio
async def test_marks_provenance_mismatch_and_drops_suspect_evidence() -> None:
    explorer = make_explorer(
        CodeTool(),
        ReadTool(path="app/other.py"),
    )

    pack = await explorer.explore(
        RepoExploreRequest(
            query="UserService",
            task_intent="find the class",
            search_mode="code",
        )
    )

    assert pack.provenance_integrity is False
    assert pack.incomplete is True
    assert pack.evidence == []
    assert any(
        issue.kind == EvidenceIssueKind.PROVENANCE_MISMATCH
        for issue in pack.issues
    )

@pytest.mark.asyncio
async def test_emits_one_correlated_trace_without_polluting_evidence_pack() -> None:
    registry = ToolRegistry()
    registry.register(SymbolTool())
    registry.register(ReadTool())
    sink = InMemoryAgentEventSink()
    runtime = ToolRuntime(event_sink=sink)
    explorer = RepoExplorer(
        repository="TechPilot",
        registry=registry,
        runtime=runtime,
        event_sink=sink,
    )

    pack = await explorer.explore(
        RepoExploreRequest(
            query="load_user",
            task_intent="understand user loading",
            search_mode="symbol",
        ),
        trace_metadata={"trace_id": "trace-explorer", "git_sha": "abc123"},
    )

    events = sink.events_for_trace("trace-explorer")
    assert [event.event_type for event in events] == [
        AgentEventType.TOOL_CALL,
        AgentEventType.TOOL_RESULT,
        AgentEventType.TOOL_CALL,
        AgentEventType.TOOL_RESULT,
        AgentEventType.EVIDENCE_HANDOFF,
    ]
    assert events[1].parent_event_id == events[0].event_id
    assert events[3].parent_event_id == events[2].event_id

    handoff = events[-1]
    assert handoff.component == "repo_explorer"
    assert handoff.output_summary == {
        "evidence_count": 1,
        "issue_count": 0,
        "provenance_integrity": True,
        "incomplete": False,
    }
    assert handoff.trace_metadata["git_sha"] == "abc123"
    assert "trace_id" not in pack.model_dump()

from app.repository.code_retrieval_tools import (
    CodeRetrievalInput,
    CodeRetrievalMatch,
    CodeRetrievalOutput,
)


class DenseCandidateTool:
    name = "search_code_dense"
    description = "fake dense code retrieval"
    input_schema = CodeRetrievalInput
    output_schema = CodeRetrievalOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 0.1
    max_retries = 0

    async def execute(
        self,
        tool_input: CodeRetrievalInput,
    ) -> CodeRetrievalOutput:
        return CodeRetrievalOutput(
            query=tool_input.query,
            matches=[
                CodeRetrievalMatch(
                    chunk_id="chunk-dense",
                    path="app/service.py",
                    symbol="UserService.load_user",
                    kind=PythonSymbolKind.METHOD,
                    line_start=2,
                    line_end=3,
                    score=0.91,
                )
            ],
            match_count=1,
        )


@pytest.mark.asyncio
async def test_dense_candidate_is_reverified_through_read_file() -> None:
    read_tool = ReadTool()
    explorer = make_explorer(
        DenseCandidateTool(),
        read_tool,
    )

    pack = await explorer.explore(
        RepoExploreRequest(
            query="find the user loading implementation",
            task_intent="understand user loading",
            search_mode="dense",
        )
    )

    assert pack.incomplete is False
    assert pack.provenance_integrity is True
    assert len(pack.evidence) == 1
    assert pack.evidence[0].file_path == "app/service.py"
    assert pack.evidence[0].symbol == "UserService.load_user"
    assert pack.evidence[0].snippet == (
        "    def load_user(self):\n"
        "        return 'user'"
    )
    assert read_tool.calls == 1

from app.repository.code_retrieval_tools import (
    CodeHybridRetrievalMatch,
    CodeHybridRetrievalOutput,
)


class HybridCandidateTool:
    name = "search_code_hybrid"
    description = "fake hybrid code retrieval"
    input_schema = CodeRetrievalInput
    output_schema = CodeHybridRetrievalOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 0.1
    max_retries = 0

    async def execute(
        self,
        tool_input: CodeRetrievalInput,
    ) -> CodeHybridRetrievalOutput:
        return CodeHybridRetrievalOutput(
            query=tool_input.query,
            matches=[
                CodeHybridRetrievalMatch(
                    chunk_id="chunk-hybrid",
                    path="app/service.py",
                    symbol="UserService.load_user",
                    kind=PythonSymbolKind.METHOD,
                    line_start=2,
                    line_end=3,
                    score=0.03,
                    keyword_rank=2,
                    dense_rank=1,
                    keyword_score=3.5,
                    dense_score=0.91,
                )
            ],
            match_count=1,
        )


@pytest.mark.asyncio
async def test_hybrid_candidate_is_reverified_through_read_file() -> None:
    read_tool = ReadTool()
    explorer = make_explorer(
        HybridCandidateTool(),
        read_tool,
    )

    pack = await explorer.explore(
        RepoExploreRequest(
            query="find the user loading implementation",
            task_intent="understand user loading",
            search_mode="hybrid",
        )
    )

    assert pack.incomplete is False
    assert pack.provenance_integrity is True
    assert len(pack.evidence) == 1
    assert pack.evidence[0].symbol == "UserService.load_user"
    assert pack.evidence[0].snippet == (
        "    def load_user(self):\n"
        "        return 'user'"
    )
    assert read_tool.calls == 1

from app.repository.module_structure_tool import (
    InspectModulesInput,
    InspectModulesOutput,
    ModuleDependencyMatch,
    ModuleStructureMatch,
    ModuleSymbolMatch,
)


class ModuleTool:
    name = "inspect_modules"
    description = "fake module structure"
    input_schema = InspectModulesInput
    output_schema = InspectModulesOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 0.1
    max_retries = 0

    async def execute(
        self,
        tool_input: InspectModulesInput,
    ) -> InspectModulesOutput:
        return InspectModulesOutput(
            modules=[
                ModuleStructureMatch(
                    path="app/service.py",
                    module="app.service",
                    internal_dependencies=[
                        ModuleDependencyMatch(
                            module="app.repository",
                            path="app/repository.py",
                            line_start=1,
                            line_end=1,
                        )
                    ],
                    symbols=[
                        ModuleSymbolMatch(
                            name="UserService",
                            kind=PythonSymbolKind.CLASS,
                            line_start=2,
                            line_end=3,
                        )
                    ],
                )
            ],
            module_count=1,
            python_file_count=1,
            parse_error_count=0,
            read_error_count=0,
            truncated=False,
        )


class ModuleReadTool(ReadTool):
    async def execute(
        self,
        tool_input: ReadFileInput,
    ) -> ReadFileOutput:
        self.calls += 1
        content = (
            "from app.repository import UserRepository\n"
            "class UserService:\n"
            "    pass\n"
        )
        return ReadFileOutput(
            path="app/service.py",
            content=content,
            size_bytes=len(content.encode("utf-8")),
        )


@pytest.mark.asyncio
async def test_module_mode_turns_structure_into_authoritative_code_evidence() -> None:
    read_tool = ModuleReadTool()
    explorer = make_explorer(ModuleTool(), read_tool)

    pack = await explorer.explore(
        RepoExploreRequest(
            query="module structure",
            task_intent="understand module dependencies",
            search_mode="module",
            limit=10,
        )
    )

    assert pack.provenance_integrity is True
    assert pack.incomplete is False
    assert [item.snippet for item in pack.evidence] == [
        "from app.repository import UserRepository",
        "class UserService:",
    ]
    assert [item.symbol for item in pack.evidence] == [None, "UserService"]
    assert read_tool.calls == 1
