from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.config import settings
from app.harness.agent_event import InMemoryAgentEventSink
from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolRuntime
from app.repository.read_boundary import RepositoryReadBoundary
from app.repository.repo_explorer import RepoExplorer
from app.repository.tools import ReadFileTool, SearchCodeTool, SearchSymbolTool
from app.research.decision_llm import DeepSeekResearchDecisionProvider
from app.research.execution import RepoExplorerActionExecutor
from app.research.repo_workload import EvidenceReportFinalizer
from app.research.unified_agent import (
    UnifiedResearchReasoner,
    build_unified_research_graph,
)


async def main() -> None:
    if not settings.deepseek_api_key.strip():
        raise RuntimeError("DEEPSEEK_API_KEY is empty")

    root = Path.cwd()
    boundary = RepositoryReadBoundary(root)

    registry = ToolRegistry()
    registry.register(SearchSymbolTool(boundary))
    registry.register(SearchCodeTool(boundary))
    registry.register(ReadFileTool(boundary))

    sink = InMemoryAgentEventSink()
    runtime = ToolRuntime(event_sink=sink)
    explorer = RepoExplorer(
        repository=root.name,
        registry=registry,
        runtime=runtime,
        event_sink=sink,
    )

    provider = DeepSeekResearchDecisionProvider(
        api_key=settings.deepseek_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )

    reasoner = UnifiedResearchReasoner(
        provider=provider,
        capabilities={
            "repo_explore": (
                "Read-only repository research. It materializes authoritative "
                "CodeEvidence through read_file. CURRENT RUNTIME supports only "
                "search_mode='symbol', 'code', or 'both'. symbol requires a "
                "focused Python class/function/method name such as "
                "'RepoExplorer' or 'ToolRuntime'. code is literal text search. "
                "both runs both strategies. Arguments: query, task_intent, "
                "search_mode, limit."
            )
        },
    )

    graph = build_unified_research_graph(
        executor=RepoExplorerActionExecutor(explorer=explorer),
        reasoner=reasoner,
        finalizer=EvidenceReportFinalizer(),
    )

    trace_id = "day32-unified-gap-loop"
    result = await graph.ainvoke(
        {
            "query": (
                "Using authoritative repository source, explain both "
                "(1) how RepoExplorer turns search candidates into "
                "authoritative evidence and "
                "(2) how ToolRuntime enforces permission checks and timeout "
                "boundaries. Both mechanisms must be directly supported."
            ),
            "max_steps": 4,
            "max_retries": 1,
        },
        context={"trace_id": trace_id},
    )

    pack = result.get("evidence_pack")
    print("termination_reason:", result["termination_reason"].value)
    print("incomplete:", result["incomplete"])
    print("step_count:", result["step_count"])
    print(
        "verification:",
        result["verification"].model_dump()
        if result.get("verification") is not None
        else None,
    )
    print(
        "evidence_paths:",
        [item.file_path for item in pack.evidence]
        if pack is not None
        else [],
    )

    events = sink.events_for_trace(trace_id)
    actions = []
    for event in events:
        action = event.trace_metadata.get("research_action")
        arguments = event.trace_metadata.get("research_action_arguments")
        reason = event.trace_metadata.get("decision_reason")
        item = (action, arguments, reason)
        if action and item not in actions:
            actions.append(item)

    print("research_actions:")
    for item in actions:
        print("  ", item)

    print(
        "tool_trace:",
        [
            (event.event_type.value, event.tool_name, event.error_code)
            for event in events
        ],
    )
    print("final_answer:")
    print(result["final_answer"])

    paths = {
        item.file_path
        for item in (pack.evidence if pack is not None else [])
    }
    assert result["termination_reason"].value == "completed"
    assert "app/repository/repo_explorer.py" in paths
    assert "app/harness/tool_runtime.py" in paths
    assert result["step_count"] >= 2


if __name__ == "__main__":
    asyncio.run(main())
