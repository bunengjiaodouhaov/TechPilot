from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

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
from app.research.execution_policy import (
    DefaultExecutionPolicy,
    ProfiledUnifiedResearchReasoner,
)
from app.research.repo_workload import EvidenceReportFinalizer
from app.research.task_router import (
    ExecutionRoute,
    HeuristicTaskRouter,
)
from app.research.unified_agent import build_unified_research_graph


PRICING_SNAPSHOT_2026_08_19 = {
    "deepseek-v4-flash": {
        "cache_hit_input_per_million": 0.0028,
        "cache_miss_input_per_million": 0.14,
        "output_per_million": 0.28,
    },
    "deepseek-v4-pro": {
        "cache_hit_input_per_million": 0.003625,
        "cache_miss_input_per_million": 0.435,
        "output_per_million": 0.87,
    },
}


class UsageCollector:
    def __init__(self) -> None:
        self.records: list[dict[str, int]] = []

    async def response_hook(self, response: httpx.Response) -> None:
        await response.aread()
        try:
            payload = response.json()
        except ValueError:
            return

        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return

        prompt = int(usage.get("prompt_tokens", 0) or 0)
        hit = int(usage.get("prompt_cache_hit_tokens", 0) or 0)
        miss_raw = usage.get("prompt_cache_miss_tokens")
        miss = (
            int(miss_raw or 0)
            if miss_raw is not None
            else max(prompt - hit, 0)
        )

        self.records.append(
            {
                "prompt_tokens": prompt,
                "prompt_cache_hit_tokens": hit,
                "prompt_cache_miss_tokens": miss,
                "completion_tokens": int(
                    usage.get("completion_tokens", 0) or 0
                ),
                "total_tokens": int(
                    usage.get("total_tokens", 0) or 0
                ),
            }
        )

    def summary(self) -> dict[str, int]:
        keys = (
            "prompt_tokens",
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
            "completion_tokens",
            "total_tokens",
        )
        return {
            key: sum(record[key] for record in self.records)
            for key in keys
        }


def estimate_cost_usd(
    *,
    model: str,
    usage: dict[str, int],
) -> float:
    pricing = PRICING_SNAPSHOT_2026_08_19[model]
    return (
        usage["prompt_cache_hit_tokens"]
        * pricing["cache_hit_input_per_million"]
        + usage["prompt_cache_miss_tokens"]
        * pricing["cache_miss_input_per_million"]
        + usage["completion_tokens"]
        * pricing["output_per_million"]
    ) / 1_000_000


def extract_read_path(query: str) -> str:
    patterns = (
        r"(?i)\bread\s+file\s+([^\s,;]+)",
        r"读取文件\s*([^\s，。；]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, query)
        if match:
            return match.group(1).rstrip(".。")
    raise ValueError(f"workflow read path not found in query: {query}")


async def run_workflow_case(
    *,
    query: str,
    expected_paths: list[str],
    boundary: RepositoryReadBoundary,
) -> dict[str, Any]:
    registry = ToolRegistry()
    registry.register(ReadFileTool(boundary))
    runtime = ToolRuntime()

    path = extract_read_path(query)
    started = time.perf_counter()
    result = await runtime.invoke(
        tool=registry.get("read_file"),
        arguments={"path": path},
    )
    latency_ms = (time.perf_counter() - started) * 1000

    actual_paths = (
        [str(result.data["path"])]
        if result.ok and result.data is not None
        else []
    )
    covered = len(set(expected_paths) & set(actual_paths))
    coverage = covered / len(expected_paths)

    return {
        "termination_reason": (
            "completed" if result.ok else "permanent_failure"
        ),
        "task_success": result.ok and coverage == 1.0,
        "evidence_coverage": coverage,
        "step_count": 1,
        "llm_calls": 0,
        "usage": {
            "prompt_tokens": 0,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "estimated_cost_usd": 0.0,
        "latency_ms": latency_ms,
        "evidence_paths": actual_paths,
    }


async def run_agent_case(
    *,
    query: str,
    expected_paths: list[str],
    boundary: RepositoryReadBoundary,
    profile,
) -> dict[str, Any]:
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

    collector = UsageCollector()

    async with httpx.AsyncClient(
        timeout=settings.llm_timeout_seconds,
        event_hooks={"response": [collector.response_hook]},
    ) as client:
        provider = DeepSeekResearchDecisionProvider(
            api_key=settings.deepseek_api_key,
            base_url=settings.llm_base_url,
            model=profile.model_name,
            timeout_seconds=settings.llm_timeout_seconds,
            max_tokens=profile.max_decision_output_tokens,
            client=client,
        )

        reasoner = ProfiledUnifiedResearchReasoner(
            provider=provider,
            capabilities={
                "repo_explore": (
                    "Read-only repository research. It materializes "
                    "authoritative CodeEvidence through read_file. CURRENT "
                    "RUNTIME supports search_mode='symbol', 'code', or 'both'. "
                    "symbol requires a focused Python class/function/method "
                    "name. code is literal text search. both runs both. "
                    "Arguments: query, task_intent, search_mode, limit."
                )
            },
            profile=profile,
        )

        graph = build_unified_research_graph(
            executor=RepoExplorerActionExecutor(explorer=explorer),
            reasoner=reasoner,
            finalizer=EvidenceReportFinalizer(),
        )

        started = time.perf_counter()
        state = await graph.ainvoke(
            {
                "query": query,
                "max_steps": profile.max_steps,
                "max_retries": profile.max_retries,
            }
        )
        latency_ms = (time.perf_counter() - started) * 1000

    pack = state.get("evidence_pack")
    actual_paths = (
        [item.file_path for item in pack.evidence]
        if pack is not None
        else []
    )
    covered = len(set(expected_paths) & set(actual_paths))
    coverage = covered / len(expected_paths)
    usage = collector.summary()

    return {
        "termination_reason": state["termination_reason"].value,
        "task_success": (
            state["termination_reason"].value == "completed"
            and coverage == 1.0
        ),
        "evidence_coverage": coverage,
        "step_count": state["step_count"],
        "llm_calls": len(collector.records),
        "usage": usage,
        "estimated_cost_usd": estimate_cost_usd(
            model=profile.model_name,
            usage=usage,
        ),
        "latency_ms": latency_ms,
        "evidence_paths": actual_paths,
    }


async def main() -> None:
    if not settings.deepseek_api_key.strip():
        raise RuntimeError(
            "DEEPSEEK_API_KEY is empty; Day33 real model workload needs it."
        )

    root = Path.cwd()
    boundary = RepositoryReadBoundary(root)
    router = HeuristicTaskRouter()
    policy = DefaultExecutionPolicy()

    cases = [
        {
            "case_id": "workflow-read-runtime",
            "query": (
                "Read file app/harness/tool_runtime.py and return the file."
            ),
            "expected_route": "workflow",
            "expected_paths": ["app/harness/tool_runtime.py"],
        },
        {
            "case_id": "light-runtime-timeout",
            "query": "How does ToolRuntime enforce timeout handling?",
            "expected_route": "light_agent",
            "expected_paths": ["app/harness/tool_runtime.py"],
        },
        {
            "case_id": "research-two-mechanisms",
            "query": (
                "Explain both how RepoExplorer turns search candidates into "
                "authoritative evidence and how ToolRuntime enforces permission "
                "checks and timeout boundaries. Both mechanisms must be "
                "directly supported by repository evidence."
            ),
            "expected_route": "research_agent",
            "expected_paths": [
                "app/repository/repo_explorer.py",
                "app/harness/tool_runtime.py",
            ],
        },
    ]

    rows = []
    for case in cases:
        routing = router.route(case["query"])
        profile = policy.resolve(routing)

        if routing.route is ExecutionRoute.WORKFLOW:
            metrics = await run_workflow_case(
                query=case["query"],
                expected_paths=case["expected_paths"],
                boundary=boundary,
            )
        else:
            metrics = await run_agent_case(
                query=case["query"],
                expected_paths=case["expected_paths"],
                boundary=boundary,
                profile=profile,
            )

        rows.append(
            {
                "case_id": case["case_id"],
                "route_correctness": (
                    routing.route.value == case["expected_route"]
                ),
                "route": routing.route.value,
                "model_tier": profile.model_tier.value,
                "model_name": profile.model_name,
                "profile": {
                    "max_steps": profile.max_steps,
                    "max_retries": profile.max_retries,
                    "max_decision_output_tokens": (
                        profile.max_decision_output_tokens
                    ),
                    "max_evidence_items": profile.max_evidence_items,
                    "evidence_snippet_characters": (
                        profile.evidence_snippet_characters
                    ),
                },
                **metrics,
            }
        )

    total_cost = sum(row["estimated_cost_usd"] for row in rows)
    total_tokens = sum(row["usage"]["total_tokens"] for row in rows)
    total_llm_calls = sum(row["llm_calls"] for row in rows)

    result = {
        "all_routes_correct": all(
            row["route_correctness"] for row in rows
        ),
        "all_tasks_successful": all(
            row["task_success"] for row in rows
        ),
        "total_llm_calls": total_llm_calls,
        "total_tokens": total_tokens,
        "estimated_cost_usd": total_cost,
        "pricing_snapshot": "DeepSeek official pricing, 2026-08-19",
        "cases": rows,
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))

    failed_cases = [
        {
            "case_id": row["case_id"],
            "route": row["route"],
            "model_name": row["model_name"],
            "termination_reason": row["termination_reason"],
            "task_success": row["task_success"],
            "evidence_coverage": row["evidence_coverage"],
            "step_count": row["step_count"],
            "llm_calls": row["llm_calls"],
            "total_tokens": row["usage"]["total_tokens"],
            "latency_ms": row["latency_ms"],
            "estimated_cost_usd": row["estimated_cost_usd"],
            "evidence_paths": row["evidence_paths"],
        }
        for row in rows
        if not row["task_success"]
    ]

    print("\n=== DAY33 FAILED CASES ===")
    print(json.dumps(failed_cases, indent=2, ensure_ascii=False))

    print("\n=== DAY33 POLICY INVARIANTS ===")
    print(
        json.dumps(
            {
                "all_routes_correct": result["all_routes_correct"],
                "workflow_used_zero_llm_calls": rows[0]["llm_calls"] == 0,
                "workflow_model_cost_zero": rows[0]["estimated_cost_usd"] == 0.0,
                "light_model_is_flash": (
                    rows[1]["model_name"] == "deepseek-v4-flash"
                ),
                "research_model_is_pro": (
                    rows[2]["model_name"] == "deepseek-v4-pro"
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
