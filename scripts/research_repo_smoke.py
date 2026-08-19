from __future__ import annotations

import asyncio
from pathlib import Path

from app.harness.agent_event import InMemoryAgentEventSink
from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolRuntime
from app.repository.read_boundary import RepositoryReadBoundary
from app.repository.repo_explorer import RepoExplorer
from app.repository.tools import ReadFileTool, SearchSymbolTool
from app.research.execution import RepoExplorerActionExecutor
from app.research.graph import build_research_graph
from app.research.repo_workload import (
    EvidenceReportFinalizer,
    RepositoryEvidenceVerifier,
    RepositoryMechanismSelector,
    SingleObjectivePlanner,
)


async def main() -> None:
    root = Path.cwd()
    boundary = RepositoryReadBoundary(root)

    registry = ToolRegistry()
    registry.register(SearchSymbolTool(boundary))
    registry.register(ReadFileTool(boundary))

    sink = InMemoryAgentEventSink()
    runtime = ToolRuntime(event_sink=sink)
    explorer = RepoExplorer(
        repository=root.name,
        registry=registry,
        runtime=runtime,
        event_sink=sink,
    )

    trace_id = "day31-techpilot-runtime-mechanism"
    graph = build_research_graph(
        executor=RepoExplorerActionExecutor(explorer=explorer),
        planner=SingleObjectivePlanner(),
        action_selector=RepositoryMechanismSelector(
            repo_query="ToolRuntime",
            search_mode="symbol",
            limit=3,
        ),
        verifier=RepositoryEvidenceVerifier(),
        finalizer=EvidenceReportFinalizer(),
    )

    result = await graph.ainvoke(
        {
            "query": (
                "How does TechPilot ToolRuntime enforce the execution boundary?"
            ),
            "max_steps": 2,
        },
        context={"trace_id": trace_id},
    )

    pack = result.get("evidence_pack")
    evidence = list(pack.evidence) if pack is not None else []
    events = sink.events_for_trace(trace_id)

    print("termination_reason:", result["termination_reason"].value)
    print("incomplete:", result["incomplete"])
    print("step_count:", result["step_count"])
    print("evidence_count:", len(evidence))
    print(
        "provenance_integrity:",
        pack.provenance_integrity if pack is not None else None,
    )
    print(
        "evidence_paths:",
        [item.file_path for item in evidence],
    )
    print(
        "trace:",
        [
            (
                event.event_type.value,
                event.tool_name,
                event.error_code,
            )
            for event in events
        ],
    )
    print("final_answer:")
    print(result["final_answer"])

    if evidence:
        first = evidence[0]
        preview = first.snippet[:500].replace("\n", "\\n")
        print("first_evidence_preview:", preview)


if __name__ == "__main__":
    asyncio.run(main())
