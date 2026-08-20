from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx

from app.core.config import settings
from app.harness.agent_event import InMemoryAgentEventSink
from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolRuntime
from app.repository.read_boundary import RepositoryReadBoundary
from app.repository.repo_explorer import RepoExplorer
from app.repository.tools import ReadFileTool, SearchCodeTool, SearchSymbolTool
from app.research.decision_llm import DeepSeekResearchDecisionProvider
from app.research.execution import RepoExplorerActionExecutor
from app.research.execution_policy import (
    ExecutionProfile,
    ProfiledUnifiedResearchReasoner,
)
from app.research.light_reasoner import LightHybridReasoner
from app.research.repo_workload import EvidenceReportFinalizer
from app.research.task_router import ExecutionRoute, ModelTier
from app.research.unified_agent import build_unified_research_graph
from scripts.research_mixed_workload import UsageCollector, estimate_cost_usd


QUERY = "How does ToolRuntime enforce timeout handling?"
EXPECTED_PATH = "app/harness/tool_runtime.py"

CAPABILITIES = {
    "repo_explore": (
        "Read-only repository research. It materializes authoritative "
        "CodeEvidence through read_file. CURRENT RUNTIME supports "
        "search_mode='symbol', 'code', or 'both'. symbol requires a focused "
        "Python class/function/method name. code is literal text search. "
        "both runs both. Arguments: query, task_intent, search_mode, limit."
    )
}


def make_profile(model: str) -> ExecutionProfile:
    return ExecutionProfile(
        route=ExecutionRoute.LIGHT_AGENT,
        model_tier=ModelTier.MEDIUM,
        model_name=model,
        max_steps=2,
        max_retries=1,
        max_decision_output_tokens=800,
        max_evidence_items=3,
        evidence_snippet_characters=2200,
    )


async def run_case(*, case_id: str, model: str, hybrid: bool):
    boundary = RepositoryReadBoundary(Path.cwd())
    registry = ToolRegistry()
    registry.register(SearchSymbolTool(boundary))
    registry.register(SearchCodeTool(boundary))
    registry.register(ReadFileTool(boundary))

    sink = InMemoryAgentEventSink()
    runtime = ToolRuntime(event_sink=sink)
    explorer = RepoExplorer(
        repository=boundary.root.name,
        registry=registry,
        runtime=runtime,
        event_sink=sink,
    )

    profile = make_profile(model)
    collector = UsageCollector()

    async with httpx.AsyncClient(
        timeout=settings.llm_timeout_seconds,
        event_hooks={"response": [collector.response_hook]},
    ) as client:
        provider = DeepSeekResearchDecisionProvider(
            api_key=settings.deepseek_api_key,
            base_url=settings.llm_base_url,
            model=model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_tokens=profile.max_decision_output_tokens,
            client=client,
        )
        base_reasoner = ProfiledUnifiedResearchReasoner(
            provider=provider,
            capabilities=CAPABILITIES,
            profile=profile,
        )
        reasoner = (
            LightHybridReasoner(delegate=base_reasoner)
            if hybrid
            else base_reasoner
        )

        graph = build_unified_research_graph(
            executor=RepoExplorerActionExecutor(explorer=explorer),
            reasoner=reasoner,
            finalizer=EvidenceReportFinalizer(),
        )

        started = time.perf_counter()
        state = await graph.ainvoke(
            {
                "query": QUERY,
                "max_steps": profile.max_steps,
                "max_retries": profile.max_retries,
            }
        )
        latency_ms = (time.perf_counter() - started) * 1000

    pack = state.get("evidence_pack")
    paths = (
        [item.file_path for item in pack.evidence]
        if pack is not None
        else []
    )
    usage = collector.summary()

    events = sink.events
    actions = []
    for event in events:
        action = event.trace_metadata.get("research_action")
        args = event.trace_metadata.get("research_action_arguments")
        if action:
            item = (action, args)
            if item not in actions:
                actions.append(item)

    return {
        "case_id": case_id,
        "model": model,
        "hybrid_first_action": hybrid,
        "termination_reason": state["termination_reason"].value,
        "task_success": (
            state["termination_reason"].value == "completed"
            and EXPECTED_PATH in paths
        ),
        "step_count": state["step_count"],
        "llm_calls": len(collector.records),
        "total_tokens": usage["total_tokens"],
        "latency_ms": latency_ms,
        "estimated_cost_usd": estimate_cost_usd(
            model=model,
            usage=usage,
        ),
        "evidence_paths": paths,
        "research_actions": actions,
    }


async def main() -> None:
    if not settings.deepseek_api_key.strip():
        raise RuntimeError("DEEPSEEK_API_KEY is empty")

    rows = [
        await run_case(
            case_id="flash-freeform",
            model="deepseek-v4-flash",
            hybrid=False,
        ),
        await run_case(
            case_id="flash-hybrid-symbol-first",
            model="deepseek-v4-flash",
            hybrid=True,
        ),
        await run_case(
            case_id="pro-freeform",
            model="deepseek-v4-pro",
            hybrid=False,
        ),
    ]

    print(
        json.dumps(
            {
                "query": QUERY,
                "comparison": (
                    "Same query, same 2200-char evidence budget, same "
                    "max_steps=2. Hybrid changes only the obvious first action."
                ),
                "cases": rows,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
