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
from app.research.focused_context import QueryFocusedProfiledReasoner
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


def profile() -> ExecutionProfile:
    return ExecutionProfile(
        route=ExecutionRoute.LIGHT_AGENT,
        model_tier=ModelTier.MEDIUM,
        model_name="deepseek-v4-flash",
        max_steps=2,
        max_retries=1,
        max_decision_output_tokens=800,
        max_evidence_items=3,
        evidence_snippet_characters=2200,
    )


async def run_case(*, case_id: str, focused: bool):
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

    selected_profile = profile()
    collector = UsageCollector()

    async with httpx.AsyncClient(
        timeout=settings.llm_timeout_seconds,
        event_hooks={"response": [collector.response_hook]},
    ) as client:
        provider = DeepSeekResearchDecisionProvider(
            api_key=settings.deepseek_api_key,
            base_url=settings.llm_base_url,
            model="deepseek-v4-flash",
            timeout_seconds=settings.llm_timeout_seconds,
            max_tokens=selected_profile.max_decision_output_tokens,
            client=client,
        )

        reasoner_cls = (
            QueryFocusedProfiledReasoner
            if focused
            else ProfiledUnifiedResearchReasoner
        )
        base_reasoner = reasoner_cls(
            provider=provider,
            capabilities=CAPABILITIES,
            profile=selected_profile,
        )
        reasoner = LightHybridReasoner(delegate=base_reasoner)

        graph = build_unified_research_graph(
            executor=RepoExplorerActionExecutor(explorer=explorer),
            reasoner=reasoner,
            finalizer=EvidenceReportFinalizer(),
        )

        started = time.perf_counter()
        state = await graph.ainvoke(
            {
                "query": QUERY,
                "max_steps": selected_profile.max_steps,
                "max_retries": selected_profile.max_retries,
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

    return {
        "case_id": case_id,
        "context_strategy": (
            "query_focused_window" if focused else "prefix"
        ),
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
            model="deepseek-v4-flash",
            usage=usage,
        ),
        "evidence_paths": paths,
    }


async def main() -> None:
    if not settings.deepseek_api_key.strip():
        raise RuntimeError("DEEPSEEK_API_KEY is empty")

    rows = [
        await run_case(
            case_id="flash-prefix-2200",
            focused=False,
        ),
        await run_case(
            case_id="flash-focused-2200",
            focused=True,
        ),
    ]

    print(
        json.dumps(
            {
                "query": QUERY,
                "controlled_variables": {
                    "model": "deepseek-v4-flash",
                    "max_steps": 2,
                    "max_retries": 1,
                    "evidence_budget_chars": 2200,
                    "first_action": "deterministic symbol-first",
                },
                "only_changed_variable": (
                    "evidence context selection: prefix vs query-focused window"
                ),
                "cases": rows,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
