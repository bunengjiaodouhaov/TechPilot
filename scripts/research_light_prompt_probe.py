from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.harness.agent_event import InMemoryAgentEventSink
from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolRuntime
from app.repository.read_boundary import RepositoryReadBoundary
from app.repository.repo_explorer import RepoExploreRequest, RepoExplorer
from app.repository.tools import ReadFileTool, SearchCodeTool, SearchSymbolTool
from app.research.contracts import ResearchAction
from app.research.decision_llm import DeepSeekResearchDecisionProvider
from app.research.execution import RepoExplorerActionExecutor
from app.research.execution_policy import (
    ExecutionProfile,
    ProfiledUnifiedResearchReasoner,
)
from app.research.light_reasoner import LightHybridReasoner
from app.research.repo_workload import EvidenceReportFinalizer
from app.research.task_router import ExecutionRoute, ModelTier
from app.research.unified_agent import (
    UnifiedResearchDecision,
    build_unified_research_graph,
)
from scripts.research_mixed_workload import UsageCollector


QUERY = "How does ToolRuntime enforce timeout handling?"

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


class RecordingProvider:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.calls: list[dict[str, str]] = []

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
        )
        return await self.inner.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )


def parse_payload(user_prompt: str) -> dict[str, Any]:
    prefix = "Current agent state JSON:\n"
    suffix = "\n\nReturn one decision JSON."
    if not user_prompt.startswith(prefix) or not user_prompt.endswith(suffix):
        raise RuntimeError("unexpected user prompt shape")
    return json.loads(user_prompt[len(prefix):-len(suffix)])


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def evidence_digest(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in payload.get("evidence", []):
        snippet = item.get("snippet", "")
        rows.append(
            {
                "file_path": item.get("file_path"),
                "symbol": item.get("symbol"),
                "line_start": item.get("line_start"),
                "line_end": item.get("line_end"),
                "visible_length": len(snippet),
                "snippet_sha16": sha(snippet),
                "asyncio.wait_for_pos": snippet.find("asyncio.wait_for"),
                "tool.timeout_seconds_pos": snippet.find(
                    "tool.timeout_seconds"
                ),
                "ToolErrorCode.TIMEOUT_pos": snippet.find(
                    "ToolErrorCode.TIMEOUT"
                ),
            }
        )
    return rows


def prompt_digest(call: dict[str, str]) -> dict[str, Any]:
    payload = parse_payload(call["user_prompt"])
    return {
        "system_prompt_sha16": sha(call["system_prompt"]),
        "user_prompt_sha16": sha(call["user_prompt"]),
        "task": payload.get("task"),
        "step_count": payload.get("step_count"),
        "max_steps": payload.get("max_steps"),
        "retry_count": payload.get("retry_count"),
        "max_retries": payload.get("max_retries"),
        "last_action": payload.get("last_action"),
        "last_tool_result": payload.get("last_tool_result"),
        "evidence_issues": payload.get("evidence_issues"),
        "execution_profile": payload.get("execution_profile"),
        "evidence": evidence_digest(payload),
    }


async def build_explorer():
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
    return explorer, sink


async def new_provider(*, collector: UsageCollector | None = None):
    hooks = (
        {"response": [collector.response_hook]}
        if collector is not None
        else None
    )
    client = httpx.AsyncClient(
        timeout=settings.llm_timeout_seconds,
        event_hooks=hooks,
    )
    inner = DeepSeekResearchDecisionProvider(
        api_key=settings.deepseek_api_key,
        base_url=settings.llm_base_url,
        model="deepseek-v4-flash",
        timeout_seconds=settings.llm_timeout_seconds,
        max_tokens=800,
        client=client,
    )
    return client, inner


async def run_graph_prompt():
    explorer, sink = await build_explorer()
    collector = UsageCollector()
    client, inner = await new_provider(collector=collector)
    recorder = RecordingProvider(inner)

    try:
        base = ProfiledUnifiedResearchReasoner(
            provider=recorder,
            capabilities=CAPABILITIES,
            profile=profile(),
        )
        hybrid = LightHybridReasoner(delegate=base)

        graph = build_unified_research_graph(
            executor=RepoExplorerActionExecutor(explorer=explorer),
            reasoner=hybrid,
            finalizer=EvidenceReportFinalizer(),
        )
        state = await graph.ainvoke(
            {
                "query": QUERY,
                "max_steps": 2,
                "max_retries": 1,
            }
        )
    finally:
        await client.aclose()

    if len(recorder.calls) != 1:
        raise RuntimeError(
            f"expected exactly one Flash call in hybrid graph, got "
            f"{len(recorder.calls)}"
        )

    pack = state.get("evidence_pack")
    paths = (
        [item.file_path for item in pack.evidence]
        if pack is not None
        else []
    )

    return {
        "state": {
            "termination_reason": state["termination_reason"].value,
            "step_count": state["step_count"],
            "evidence_paths": paths,
        },
        "call": recorder.calls[0],
        "usage": collector.summary(),
    }


async def run_fixed_prompt():
    explorer, _ = await build_explorer()
    pack = await explorer.explore(
        RepoExploreRequest(
            query="ToolRuntime",
            task_intent=(
                "Materialize ToolRuntime as controlled authoritative evidence."
            ),
            search_mode="symbol",
            limit=5,
        ),
        trace_metadata={"trace_id": "day33-prompt-probe-fixed"},
    )

    client, inner = await new_provider()
    recorder = RecordingProvider(inner)
    try:
        reasoner = ProfiledUnifiedResearchReasoner(
            provider=recorder,
            capabilities=CAPABILITIES,
            profile=profile(),
        )
        decision = await reasoner.decide(
            {
                "query": QUERY,
                "normalized_task": QUERY,
                "step_count": 1,
                "max_steps": 2,
                "retry_count": 0,
                "max_retries": 1,
                "last_action": ResearchAction(
                    tool_name="repo_explore",
                    arguments={
                        "query": "ToolRuntime",
                        "task_intent": QUERY,
                        "search_mode": "symbol",
                        "limit": 5,
                    },
                    reason="Controlled symbol-first acquisition.",
                ),
                "last_tool_result": None,
                "evidence_pack": pack,
            }
        )
    finally:
        await client.aclose()

    if len(recorder.calls) != 1:
        raise RuntimeError("expected one fixed-evidence Flash call")

    return {
        "decision": decision.model_dump(mode="json"),
        "call": recorder.calls[0],
    }


def diff_digests(
    graph: dict[str, Any],
    fixed: dict[str, Any],
) -> dict[str, Any]:
    keys = [
        "system_prompt_sha16",
        "task",
        "step_count",
        "max_steps",
        "retry_count",
        "max_retries",
        "last_action",
        "last_tool_result",
        "evidence_issues",
        "execution_profile",
        "evidence",
    ]
    return {
        key: {
            "same": graph.get(key) == fixed.get(key),
            "graph": graph.get(key),
            "fixed": fixed.get(key),
        }
        for key in keys
        if graph.get(key) != fixed.get(key)
    }


async def replay_exact_prompt(call: dict[str, str], n: int = 4):
    rows = []
    for index in range(1, n + 1):
        collector = UsageCollector()
        client, provider = await new_provider(collector=collector)
        try:
            raw = await provider.generate_json(
                system_prompt=call["system_prompt"],
                user_prompt=call["user_prompt"],
            )
        finally:
            await client.aclose()

        decision = UnifiedResearchDecision.model_validate(raw)
        rows.append(
            {
                "replay": index,
                "kind": decision.kind.value,
                "reason": decision.reason,
                "action": (
                    decision.action.model_dump()
                    if decision.action is not None
                    else None
                ),
                "usage": collector.summary(),
            }
        )
    return rows


async def main() -> None:
    if not settings.deepseek_api_key.strip():
        raise RuntimeError("DEEPSEEK_API_KEY is empty")

    graph = await run_graph_prompt()
    fixed = await run_fixed_prompt()

    graph_digest = prompt_digest(graph["call"])
    fixed_digest = prompt_digest(fixed["call"])

    replays = await replay_exact_prompt(graph["call"], n=4)

    kinds = [row["kind"] for row in replays]
    result = {
        "graph_run": graph["state"],
        "graph_prompt": graph_digest,
        "fixed_decision": fixed["decision"],
        "fixed_prompt": fixed_digest,
        "prompt_differences": diff_digests(
            graph_digest,
            fixed_digest,
        ),
        "exact_graph_prompt_replays": replays,
        "replay_kind_counts": {
            kind: kinds.count(kind)
            for kind in sorted(set(kinds))
        },
        "interpretation": {
            "prompt_difference": (
                "If graph/fixed evidence digests differ, the previous A/B did "
                "not actually give Flash the same decision context."
            ),
            "replay_variance": (
                "If identical prompt replays produce both ACT and COMPLETE, "
                "the Flash decision is run-level unstable even with fixed "
                "context."
            ),
            "stable_act": (
                "If identical graph prompt is consistently ACT while the "
                "fixed prompt is COMPLETE, inspect the printed state/prompt "
                "differences before changing model policy."
            ),
        },
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
