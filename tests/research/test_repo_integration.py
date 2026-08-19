from __future__ import annotations

import pytest

from app.harness.agent_event import AgentEventType, InMemoryAgentEventSink
from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolRuntime
from app.repository.read_boundary import RepositoryReadBoundary
from app.repository.repo_explorer import RepoExplorer
from app.repository.tools import ReadFileTool, SearchSymbolTool
from app.research.contracts import TerminationReason
from app.research.execution import RepoExplorerActionExecutor
from app.research.graph import build_research_graph
from app.research.repo_workload import (
    EvidenceReportFinalizer,
    RepositoryEvidenceVerifier,
    RepositoryMechanismSelector,
    SingleObjectivePlanner,
)


@pytest.mark.asyncio
async def test_real_repo_capability_materializes_authoritative_evidence(
    tmp_path,
) -> None:
    source = tmp_path / "service.py"
    source.write_text(
        "class PaymentService:\n"
        "    def authorize(self):\n"
        "        return True\n",
        encoding="utf-8",
    )

    boundary = RepositoryReadBoundary(tmp_path)
    registry = ToolRegistry()
    registry.register(SearchSymbolTool(boundary))
    registry.register(ReadFileTool(boundary))

    sink = InMemoryAgentEventSink()
    runtime = ToolRuntime(event_sink=sink)
    explorer = RepoExplorer(
        repository="fixture-repo",
        registry=registry,
        runtime=runtime,
        event_sink=sink,
    )

    graph = build_research_graph(
        executor=RepoExplorerActionExecutor(explorer=explorer),
        planner=SingleObjectivePlanner(),
        action_selector=RepositoryMechanismSelector(
            repo_query="PaymentService",
        ),
        verifier=RepositoryEvidenceVerifier(),
        finalizer=EvidenceReportFinalizer(),
    )

    result = await graph.ainvoke(
        {
            "query": "How is payment authorization implemented?",
            "max_steps": 2,
        },
        context={"trace_id": "day31-real-repo"},
    )

    assert result["termination_reason"] is TerminationReason.COMPLETED
    assert result["step_count"] == 1
    assert result["evidence_pack"].provenance_integrity is True
    assert len(result["evidence_pack"].evidence) == 1
    assert result["evidence_pack"].evidence[0].file_path == "service.py"

    events = sink.events_for_trace("day31-real-repo")
    assert [event.event_type for event in events] == [
        AgentEventType.TOOL_CALL,
        AgentEventType.TOOL_RESULT,
        AgentEventType.TOOL_CALL,
        AgentEventType.TOOL_RESULT,
        AgentEventType.EVIDENCE_HANDOFF,
    ]
    assert [event.tool_name for event in events[:4]] == [
        "search_symbol",
        "search_symbol",
        "read_file",
        "read_file",
    ]
    assert all(
        event.trace_metadata.get("research_action") == "repo_explore"
        for event in events
    )
