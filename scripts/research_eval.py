from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.harness.agent_event import InMemoryAgentEventSink
from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolRuntime
from app.repository.read_boundary import RepositoryReadBoundary
from app.repository.repo_explorer import RepoExplorer
from app.repository.tools import ReadFileTool, SearchSymbolTool
from app.research.evaluation import ResearchGoldenCase, evaluate_research_run
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
    cases_path = root / "evals/research/golden_cases.json"
    raw_cases = json.loads(cases_path.read_text(encoding="utf-8"))
    cases = [ResearchGoldenCase.model_validate(item) for item in raw_cases]

    boundary = RepositoryReadBoundary(root)
    registry = ToolRegistry()
    registry.register(SearchSymbolTool(boundary))
    registry.register(ReadFileTool(boundary))

    rows: list[dict] = []

    for case in cases:
        sink = InMemoryAgentEventSink()
        runtime = ToolRuntime(event_sink=sink)
        explorer = RepoExplorer(
            repository=root.name,
            registry=registry,
            runtime=runtime,
            event_sink=sink,
        )

        trace_id = f"research-eval-{case.case_id}"
        graph = build_research_graph(
            executor=RepoExplorerActionExecutor(explorer=explorer),
            planner=SingleObjectivePlanner(),
            action_selector=RepositoryMechanismSelector(
                repo_query=case.repo_query,
                search_mode=case.search_mode,
                limit=5,
            ),
            verifier=RepositoryEvidenceVerifier(),
            finalizer=EvidenceReportFinalizer(),
        )

        state = await graph.ainvoke(
            {
                "query": case.query,
                "max_steps": case.max_steps,
            },
            context={"trace_id": trace_id},
        )
        events = sink.events_for_trace(trace_id)
        result = evaluate_research_run(
            case=case,
            state=state,
            events=events,
        )
        rows.append(result.model_dump(mode="json"))

    print(json.dumps(rows, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
