from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from app.core.config import settings
from app.harness.agent_event import InMemoryAgentEventSink
from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolRuntime
from app.repository.read_boundary import RepositoryReadBoundary
from app.repository.repo_explorer import RepoExploreRequest, RepoExplorer
from app.repository.tools import ReadFileTool, SearchSymbolTool
from app.research.contracts import ResearchAction
from app.research.decision_llm import DeepSeekResearchDecisionProvider
from app.research.execution_policy import (
    ExecutionProfile,
    ProfiledUnifiedResearchReasoner,
)
from app.research.task_router import ExecutionRoute, ModelTier
from app.research.unified_agent import UnifiedDecisionKind
from scripts.research_mixed_workload import UsageCollector, estimate_cost_usd


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


def profile(*, model: str, snippet_chars: int) -> ExecutionProfile:
    # Keep route/tier/budgets fixed so only model + evidence context vary.
    return ExecutionProfile(
        route=ExecutionRoute.LIGHT_AGENT,
        model_tier=ModelTier.MEDIUM,
        model_name=model,
        max_steps=2,
        max_retries=1,
        max_decision_output_tokens=800,
        max_evidence_items=3,
        evidence_snippet_characters=snippet_chars,
    )


async def materialize_fixed_evidence():
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

    pack = await explorer.explore(
        RepoExploreRequest(
            query="ToolRuntime",
            task_intent=(
                "Materialize the ToolRuntime implementation as fixed "
                "authoritative evidence for a controlled Day33 A/B."
            ),
            search_mode="symbol",
            limit=5,
        ),
        trace_metadata={"trace_id": "day33-fixed-evidence"},
    )

    if len(pack.evidence) != 1:
        raise RuntimeError(
            f"expected exactly one ToolRuntime evidence item, got "
            f"{len(pack.evidence)}"
        )

    return pack


def support_diagnostics(snippet: str, snippet_chars: int) -> dict:
    visible = snippet[:snippet_chars]

    markers = {
        "asyncio.wait_for": visible.find("asyncio.wait_for"),
        "tool.timeout_seconds": visible.find("tool.timeout_seconds"),
        "ToolErrorCode.TIMEOUT": visible.find("ToolErrorCode.TIMEOUT"),
    }

    return {
        "snippet_chars": snippet_chars,
        "visible_length": len(visible),
        "contains_asyncio_wait_for": markers["asyncio.wait_for"] >= 0,
        "contains_tool_timeout_seconds": markers["tool.timeout_seconds"] >= 0,
        "contains_timeout_error_code": markers["ToolErrorCode.TIMEOUT"] >= 0,
        "marker_positions_in_visible_context": markers,
        "direct_timeout_support": (
            markers["asyncio.wait_for"] >= 0
            and markers["tool.timeout_seconds"] >= 0
        ),
    }


async def decide_once(*, pack, model: str, snippet_chars: int):
    selected_profile = profile(
        model=model,
        snippet_chars=snippet_chars,
    )
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
            max_tokens=selected_profile.max_decision_output_tokens,
            client=client,
        )
        reasoner = ProfiledUnifiedResearchReasoner(
            provider=provider,
            capabilities=CAPABILITIES,
            profile=selected_profile,
        )

        state = {
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
                    "task_intent": (
                        "Find the ToolRuntime implementation for timeout "
                        "handling."
                    ),
                    "search_mode": "symbol",
                    "limit": 5,
                },
                reason="Fixed deterministic evidence acquisition.",
            ),
            "last_tool_result": None,
            "evidence_pack": pack,
        }

        decision = await reasoner.decide(state)

    usage = collector.summary()
    support = support_diagnostics(
        pack.evidence[0].snippet,
        snippet_chars,
    )

    completed = decision.kind is UnifiedDecisionKind.COMPLETE
    grounding_safe = (not completed) or support["direct_timeout_support"]

    return {
        "case_id": f"{model}-{snippet_chars}",
        "model": model,
        "snippet_chars": snippet_chars,
        "decision_kind": decision.kind.value,
        "decision_reason": decision.reason,
        "unresolved_questions": decision.unresolved_questions,
        "action": (
            decision.action.model_dump()
            if decision.action is not None
            else None
        ),
        "context_support": support,
        "grounding_safe": grounding_safe,
        "usage": usage,
        "estimated_cost_usd": estimate_cost_usd(
            model=model,
            usage=usage,
        ),
    }


async def main() -> None:
    if not settings.deepseek_api_key.strip():
        raise RuntimeError("DEEPSEEK_API_KEY is empty")

    pack = await materialize_fixed_evidence()
    item = pack.evidence[0]

    full_marker_positions = {
        "asyncio.wait_for": item.snippet.find("asyncio.wait_for"),
        "tool.timeout_seconds": item.snippet.find("tool.timeout_seconds"),
        "ToolErrorCode.TIMEOUT": item.snippet.find("ToolErrorCode.TIMEOUT"),
    }

    experiments = [
        ("deepseek-v4-flash", 2200),
        ("deepseek-v4-flash", 5000),
        ("deepseek-v4-pro", 2200),
        ("deepseek-v4-pro", 5000),
    ]

    rows = []
    for model, snippet_chars in experiments:
        rows.append(
            await decide_once(
                pack=pack,
                model=model,
                snippet_chars=snippet_chars,
            )
        )

    result = {
        "fixed_evidence": {
            "file_path": item.file_path,
            "symbol": item.symbol,
            "line_start": item.line_start,
            "line_end": item.line_end,
            "full_snippet_length": len(item.snippet),
            "full_marker_positions": full_marker_positions,
        },
        "experiment_control": (
            "Same ToolRuntime EvidencePack, same query, same prior action, "
            "same max_steps/retries/output/evidence-item budgets. Only model "
            "and visible snippet characters vary."
        ),
        "cases": rows,
        "interpretation_rules": {
            "complete_with_direct_support": (
                "Grounded success: visible context directly contains timeout "
                "implementation markers."
            ),
            "complete_without_direct_support": (
                "Potential overclaim: model completed without direct support "
                "in its decision context."
            ),
            "act_with_direct_support": (
                "Conservative false negative: support is visible but model "
                "still asks for another action."
            ),
            "act_without_direct_support": (
                "Correctly cautious: model recognizes the visible context is "
                "insufficient."
            ),
        },
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
