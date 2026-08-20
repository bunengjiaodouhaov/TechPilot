from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.config import settings
from app.harness.agent_event import InMemoryAgentEventSink
from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolRuntime
from app.repository.read_boundary import RepositoryReadBoundary
from app.repository.repo_explorer import RepoExplorer
from app.repository.tools import (
    ReadFileTool,
    SearchCodeTool,
    SearchSymbolTool,
)
from app.research.decision_llm import DeepSeekResearchDecisionProvider
from app.research.execution import RepoExplorerActionExecutor
from app.research.graph import build_research_graph
from app.research.llm_components import (
    LLMResearchActionSelector,
    LLMResearchPlanner,
)
from app.research.repo_workload import (
    EvidenceReportFinalizer,
    RepositoryEvidenceVerifier,
)


async def main() -> None:
    if not settings.deepseek_api_key.strip():
        raise RuntimeError(
            "DEEPSEEK_API_KEY is empty; configure it in .env before the real LLM smoke."
        )

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

    planner = LLMResearchPlanner(provider=provider)
    selector = LLMResearchActionSelector(
        provider=provider,
        capabilities={
            "repo_explore": (
                "Read-only repository research that materializes authoritative "
                "CodeEvidence through read_file. CURRENT RUNTIME supports only "
                "search_mode='symbol', 'code', or 'both'. "
                "symbol requires a focused exact Python class/function/method "
                "name such as 'ToolRuntime'. code performs literal text search "
                "and is better for implementation phrases. both runs both "
                "strategies. Arguments: query, task_intent, search_mode, limit."
            )
        },
    )

    trace_id = "day32-llm-research"
    graph = build_research_graph(
        executor=RepoExplorerActionExecutor(explorer=explorer),
        planner=planner,
        action_selector=selector,
        verifier=RepositoryEvidenceVerifier(),
        finalizer=EvidenceReportFinalizer(),
    )

    result = await graph.ainvoke(
        {
            "query": (
                "How does TechPilot ToolRuntime enforce input/output validation "
                "and timeout handling?"
            ),
            "max_steps": 3,
            "max_retries": 1,
        },
        context={"trace_id": trace_id},
    )

    print(
        "plan:",
        [step.model_dump() for step in result["plan"]],
    )
    print("termination_reason:", result["termination_reason"].value)
    print("incomplete:", result["incomplete"])
    print("step_count:", result["step_count"])

    pack = result.get("evidence_pack")
    print(
        "evidence_count:",
        len(pack.evidence) if pack is not None else 0,
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


if __name__ == "__main__":
    asyncio.run(main())
