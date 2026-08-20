from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.harness.agent_event import AgentEventType, InMemoryAgentEventSink
from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import ToolRuntime
from app.repository.read_boundary import (
    DEFAULT_EXCLUDED_DIRS,
    RepositoryReadBoundary,
)
from app.repository.repo_explorer import RepoExplorer
from app.repository.tools import ReadFileTool, SearchCodeTool, SearchSymbolTool
from app.research.context_metrics import (
    DecisionContextRequirement,
    evaluate_context_coverage,
)
from app.research.contracts import ResearchAction
from app.research.decision_llm import DeepSeekResearchDecisionProvider
from app.research.execution import RepoExplorerActionExecutor
from app.research.execution_policy import (
    DefaultExecutionPolicy,
    ProfiledUnifiedResearchReasoner,
)
from app.research.execution_strategy import (
    DefaultExecutionStrategyPolicy,
    EvidenceContextStrategy,
)
from app.research.focused_context import QueryFocusedProfiledReasoner
from app.research.light_reasoner import LightHybridReasoner
from app.research.repo_workload import EvidenceReportFinalizer
from app.research.task_router import (
    ExecutionRoute,
    HeuristicTaskRouter,
    ModelTier,
    RoutingDecision,
)
from app.research.unified_agent import (
    UnifiedDecisionKind,
    UnifiedResearchDecision,
    build_unified_research_graph,
)


REALISTIC_EVAL_EXCLUDED_DIRS = DEFAULT_EXCLUDED_DIRS | frozenset({"eval", "evals", ".pytest_cache"})


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

CAPABILITIES = {
    "repo_explore": (
        "Read-only repository research. It materializes authoritative "
        "CodeEvidence through read_file. CURRENT RUNTIME supports "
        "search_mode='symbol', 'code', or 'both'. symbol requires a focused "
        "Python class/function/method name. code is literal text search. "
        "both runs both. Arguments: query, task_intent, search_mode, limit."
    )
}

# The benchmark fixture itself contains gold answers and query strings.
# SearchCodeTool scans all readable repository text, so evals/ must not be part
# of the corpus under test or the benchmark can retrieve its own labels.
EVAL_EXCLUDED_DIRS = DEFAULT_EXCLUDED_DIRS | frozenset({"evals", "scripts"})


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
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
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


def estimate_cost_usd(*, model: str, usage: dict[str, int]) -> float:
    pricing = PRICING_SNAPSHOT_2026_08_19[model]
    return (
        usage["prompt_cache_hit_tokens"]
        * pricing["cache_hit_input_per_million"]
        + usage["prompt_cache_miss_tokens"]
        * pricing["cache_miss_input_per_million"]
        + usage["completion_tokens"]
        * pricing["output_per_million"]
    ) / 1_000_000


class RecordingDecisionProvider:
    """Record semantic decision prompts for evaluator-only inspection."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.user_prompts: list[str] = []

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        self.user_prompts.append(user_prompt)
        return await self._inner.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )


class WrongFirstActionReasoner:
    """Inject exactly one known-wrong first action, then delegate normally."""

    def __init__(self, *, delegate, raw_action: dict[str, Any]) -> None:
        self._delegate = delegate
        self._raw_action = raw_action
        self._used = False

    async def decide(self, state) -> UnifiedResearchDecision:
        if not self._used:
            self._used = True
            return UnifiedResearchDecision(
                kind=UnifiedDecisionKind.ACT,
                reason=(
                    "Controlled Day34 recovery injection: execute one known-wrong "
                    "first action, then return to the normal semantic reasoner."
                ),
                unresolved_questions=[
                    "The injected action is intentionally not expected to "
                    "materialize useful evidence."
                ],
                action=ResearchAction(
                    tool_name=self._raw_action["tool_name"],
                    arguments=dict(self._raw_action["arguments"]),
                    reason="Controlled wrong-first-action recovery probe.",
                ),
            )

        decision = self._delegate.decide(state)
        if hasattr(decision, "__await__"):
            return await decision
        return decision


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Day34 24-case tiered-vs-always-large robustness matrix."
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run only the named case_id. May be repeated.",
    )
    parser.add_argument(
        "--category",
        action="append",
        default=[],
        help="Run only the named category. May be repeated.",
    )
    return parser.parse_args()


def load_cases(root: Path) -> list[dict[str, Any]]:
    path = root / "evals/research/mixed_workload_cases.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("mixed workload must be a non-empty JSON list")
    return raw


def filter_cases(
    cases: list[dict[str, Any]],
    *,
    case_ids: list[str],
    categories: list[str],
) -> list[dict[str, Any]]:
    selected = cases

    if case_ids:
        wanted = set(case_ids)
        selected = [
            case for case in selected
            if case["case_id"] in wanted
        ]
        missing = wanted - {case["case_id"] for case in selected}
        if missing:
            raise ValueError(
                f"unknown case_id(s): {sorted(missing)}"
            )

    if categories:
        wanted_categories = set(categories)
        selected = [
            case for case in selected
            if case["category"] in wanted_categories
        ]
        if not selected:
            raise ValueError(
                f"no cases matched categories: {sorted(wanted_categories)}"
            )

    return selected


def build_boundary(root: Path) -> RepositoryReadBoundary:
    # Eval boundary: natural repo noise in, benchmark leakage out.
    return RepositoryReadBoundary(
        root,
        excluded_dirs=REALISTIC_EVAL_EXCLUDED_DIRS,
    )


def build_context_requirements(
    raw: list[dict[str, Any]],
) -> list[DecisionContextRequirement]:
    return [
        DecisionContextRequirement.model_validate(item)
        for item in raw
    ]


def source_paths_from_pack(pack) -> list[str]:
    if pack is None:
        return []
    return sorted({item.file_path for item in pack.evidence})


def path_coverage(
    expected_paths: list[str],
    actual_paths: list[str],
) -> float:
    expected = set(expected_paths)
    actual = set(actual_paths)
    return (
        len(expected & actual) / len(expected)
        if expected
        else 1.0
    )


def extract_final_decision_evidence(
    prompts: list[str],
) -> tuple[list[str], list[str]]:
    """
    Extract only the evidence visible in the final semantic decision prompt.

    This intentionally excludes:
    - the task text,
    - allowed capability descriptions,
    - earlier decision prompts.

    It prevents benchmark marker leakage from the query and prevents unioning
    facts that were never simultaneously visible to one semantic decision.
    """
    if not prompts:
        return [], []

    prompt = prompts[-1]
    marker = "Current agent state JSON:\n"
    start = prompt.rfind(marker)
    if start < 0:
        return [], []

    payload_start = start + len(marker)
    end_marker = "\n\nReturn one decision JSON."
    end = prompt.find(end_marker, payload_start)
    if end < 0:
        return [], []

    try:
        payload = json.loads(prompt[payload_start:end])
    except json.JSONDecodeError:
        return [], []

    raw_evidence = payload.get("evidence")
    if not isinstance(raw_evidence, list):
        return [], []

    snippets: list[str] = []
    paths: list[str] = []

    for item in raw_evidence:
        if not isinstance(item, dict):
            continue

        snippet = item.get("snippet")
        if isinstance(snippet, str):
            snippets.append(snippet)

        path = item.get("file_path")
        if isinstance(path, str) and path not in paths:
            paths.append(path)

    return snippets, paths


def action_sequence_from_events(events) -> list[dict[str, Any]]:
    """
    One EVIDENCE_HANDOFF represents one semantic repo_explore action.
    Underlying search/read TOOL_CALL events are implementation details.
    """
    actions: list[dict[str, Any]] = []

    for event in events:
        if event.event_type is not AgentEventType.EVIDENCE_HANDOFF:
            continue

        metadata = event.trace_metadata
        tool_name = metadata.get("research_action")
        arguments = metadata.get("research_action_arguments")
        if not tool_name:
            continue

        actions.append(
            {
                "tool_name": str(tool_name),
                "arguments": (
                    dict(arguments)
                    if isinstance(arguments, dict)
                    else {}
                ),
                "evidence_count": int(
                    event.output_summary.get("evidence_count", 0) or 0
                ),
                "issue_count": int(
                    event.output_summary.get("issue_count", 0) or 0
                ),
            }
        )

    return actions


def action_signature(action: dict[str, Any]) -> str:
    arguments = action.get("arguments") or {}
    normalized = {
        "tool_name": action.get("tool_name"),
        "query": " ".join(
            str(arguments.get("query", "")).casefold().split()
        ),
        "search_mode": arguments.get("search_mode"),
    }
    return json.dumps(
        normalized,
        sort_keys=True,
        ensure_ascii=False,
    )


def control_metrics(
    *,
    case: dict[str, Any],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    signatures = [action_signature(action) for action in actions]
    consecutive_duplicate_count = sum(
        signatures[index] == signatures[index - 1]
        for index in range(1, len(signatures))
    )
    zero_evidence_action_count = sum(
        int(action.get("evidence_count", 0)) == 0
        for action in actions
    )
    evidence_gain_action_count = sum(
        int(action.get("evidence_count", 0)) > 0
        for action in actions
    )

    injection = case.get("inject_wrong_first_action")
    recovered: bool | None = None
    injected_first_action_correct: bool | None = None

    if injection is not None:
        if actions:
            expected_first = {
                "tool_name": injection["tool_name"],
                "arguments": injection["arguments"],
            }
            actual_first = {
                "tool_name": actions[0]["tool_name"],
                "arguments": actions[0]["arguments"],
            }
            injected_first_action_correct = (
                actual_first == expected_first
            )
            recovered = bool(
                injected_first_action_correct
                and len(actions) >= 2
                and action_signature(actions[1])
                != action_signature(actions[0])
            )
        else:
            injected_first_action_correct = False
            recovered = False

    all_allowed = all(
        action["tool_name"] == "repo_explore"
        for action in actions
    )
    tool_action_correctness = bool(
        all_allowed
        and consecutive_duplicate_count == 0
        and (
            recovered is not False
            if injection is not None
            else True
        )
    )

    return {
        "tool_action_correctness": tool_action_correctness,
        "consecutive_duplicate_action_count": (
            consecutive_duplicate_count
        ),
        "zero_evidence_action_count": zero_evidence_action_count,
        "evidence_gain_action_count": evidence_gain_action_count,
        "unique_action_count": len(set(signatures)),
        "recovered_from_wrong_first_action": recovered,
        "injected_first_action_correct": injected_first_action_correct,
    }


def forced_research_decision() -> RoutingDecision:
    return RoutingDecision(
        route=ExecutionRoute.RESEARCH_AGENT,
        model_tier=ModelTier.LARGE,
        reason=(
            "Day34 Always-Large baseline: every semantic repository research "
            "task uses the full Pro Research Agent profile."
        ),
        signals=["always-large-baseline"],
    )


def extract_workflow_read_path(query: str) -> str:
    patterns = (
        re.compile(
            r"\b(?:read|open|show|cat)\b.{0,32}"
            r"\b(?:file|path)\b\s*([^\s,;]+)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:读取|打开|显示).{0,12}(?:文件|路径)"
            r"\s*([^\s，。；]+)"
        ),
    )

    for pattern in patterns:
        match = pattern.search(query)
        if match:
            return match.group(1).rstrip(".,;:。；：")

    raise ValueError(
        "Day34 workflow fixtures currently require an explicit file path"
    )


async def run_workflow_case(
    *,
    case: dict[str, Any],
    root: Path,
    strategy_name: str,
    router_observed_route: str,
    route_correctness: bool | None,
) -> dict[str, Any]:
    boundary = build_boundary(root)
    registry = ToolRegistry()
    registry.register(ReadFileTool(boundary))

    sink = InMemoryAgentEventSink()
    runtime = ToolRuntime(event_sink=sink)

    path = extract_workflow_read_path(case["query"])
    trace_id = f"day34v2-{strategy_name}-{case['case_id']}"

    started = time.perf_counter()
    result = await runtime.invoke(
        tool=registry.get("read_file"),
        arguments={"path": path},
        trace_metadata={"trace_id": trace_id},
    )
    latency_ms = (time.perf_counter() - started) * 1000

    actual_paths = (
        [str(result.data["path"])]
        if result.ok and result.data is not None
        else []
    )
    coverage = path_coverage(
        case["expected_source_paths"],
        actual_paths,
    )
    completed = bool(result.ok)
    task_success = bool(completed and coverage == 1.0)
    termination_correctness = (
        completed
        if case["expected_outcome"] == "completed"
        else not completed
    )
    outcome_correctness = bool(
        task_success
        if case["expected_outcome"] == "completed"
        else termination_correctness
    )
    case_pass = bool(
        outcome_correctness
        and (
            route_correctness
            if strategy_name == "tiered"
            else True
        )
    )

    events = sink.events_for_trace(trace_id)

    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "strategy": strategy_name,
        "router_observed_route": router_observed_route,
        "execution_route": "workflow",
        "route_correctness": route_correctness,
        "model_tier": "none",
        "model_name": None,
        "context_strategy": "none",
        "deterministic_symbol_first": False,
        "expected_outcome": case["expected_outcome"],
        "task_success": task_success,
        "outcome_correctness": outcome_correctness,
        "case_pass": case_pass,
        "source_coverage": coverage,
        "decision_context_applicable": False,
        "decision_context_source_coverage": 1.0,
        "decision_context_coverage": 1.0,
        "grounded_completion": task_success,
        "provenance_integrity": bool(result.ok),
        "tool_action_correctness": bool(result.ok),
        "consecutive_duplicate_action_count": 0,
        "zero_evidence_action_count": 0,
        "evidence_gain_action_count": 1 if result.ok else 0,
        "unique_action_count": 1,
        "recovered_from_wrong_first_action": None,
        "injected_first_action_correct": None,
        "termination_correctness": termination_correctness,
        "termination_reason": (
            "completed" if completed else "permanent_failure"
        ),
        "step_count": 1,
        "tool_calls": sum(
            event.event_type is AgentEventType.TOOL_CALL
            for event in events
        ),
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
        "decision_context_evidence_paths": [],
        "action_sequence": [
            {
                "tool_name": "read_file",
                "arguments": {"path": path},
                "evidence_count": 1 if result.ok else 0,
                "issue_count": 0 if result.ok else 1,
            }
        ],
        "missing_context_requirement_ids": [],
    }


async def run_agent_case(
    *,
    case: dict[str, Any],
    root: Path,
    profile,
    strategy,
    strategy_name: str,
    router_observed_route: str,
    route_correctness: bool | None,
) -> dict[str, Any]:
    boundary = build_boundary(root)
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
    trace_id = f"day34v2-{strategy_name}-{case['case_id']}"

    async with httpx.AsyncClient(
        timeout=settings.llm_timeout_seconds,
        event_hooks={"response": [collector.response_hook]},
    ) as client:
        raw_provider = DeepSeekResearchDecisionProvider(
            api_key=settings.deepseek_api_key,
            base_url=settings.llm_base_url,
            model=profile.model_name,
            timeout_seconds=settings.llm_timeout_seconds,
            max_tokens=profile.max_decision_output_tokens,
            client=client,
        )
        provider = RecordingDecisionProvider(raw_provider)

        reasoner_cls = (
            QueryFocusedProfiledReasoner
            if (
                strategy.evidence_context_strategy
                is EvidenceContextStrategy.QUERY_FOCUSED
            )
            else ProfiledUnifiedResearchReasoner
        )
        base_reasoner = reasoner_cls(
            provider=provider,
            capabilities=CAPABILITIES,
            profile=profile,
        )

        reasoner = base_reasoner
        if strategy.deterministic_symbol_first:
            reasoner = LightHybridReasoner(delegate=reasoner)

        injection = case.get("inject_wrong_first_action")
        if injection is not None:
            reasoner = WrongFirstActionReasoner(
                delegate=reasoner,
                raw_action=injection,
            )

        graph = build_unified_research_graph(
            executor=RepoExplorerActionExecutor(explorer=explorer),
            reasoner=reasoner,
            finalizer=EvidenceReportFinalizer(),
        )

        started = time.perf_counter()
        state = await graph.ainvoke(
            {
                "query": case["query"],
                "max_steps": profile.max_steps,
                "max_retries": profile.max_retries,
            },
            context={"trace_id": trace_id},
        )
        latency_ms = (time.perf_counter() - started) * 1000

    pack = state.get("evidence_pack")
    actual_paths = source_paths_from_pack(pack)
    source_coverage = path_coverage(
        case["expected_source_paths"],
        actual_paths,
    )

    final_snippets, final_context_paths = (
        extract_final_decision_evidence(provider.user_prompts)
    )
    context_eval = evaluate_context_coverage(
        expected_source_paths=case["expected_source_paths"],
        actual_source_paths=final_context_paths,
        visible_contexts=final_snippets,
        requirements=build_context_requirements(
            case.get("context_requirements", [])
        ),
        completed=(
            state["termination_reason"].value == "completed"
        ),
    )

    termination_reason = state["termination_reason"].value
    completed = termination_reason == "completed"
    provenance_integrity = (
        pack.provenance_integrity
        if pack is not None
        else None
    )

    grounded_completion = bool(
        completed
        and source_coverage == 1.0
        and context_eval.grounded_completion
        and provenance_integrity is True
    )
    task_success = grounded_completion

    expected_outcome = case["expected_outcome"]
    if expected_outcome == "completed":
        termination_correctness = completed
        outcome_correctness = task_success
    elif expected_outcome == "insufficient_evidence":
        expected_terminations = set(
            case.get(
                "expected_termination_any",
                ["no_actionable_path", "max_steps"],
            )
        )
        termination_correctness = bool(
            not completed
            and termination_reason in expected_terminations
        )
        outcome_correctness = bool(
            termination_correctness
            and provenance_integrity is not False
        )
    else:
        raise ValueError(
            f"unknown expected_outcome: {expected_outcome}"
        )

    events = sink.events_for_trace(trace_id)
    actions = action_sequence_from_events(events)
    controls = control_metrics(
        case=case,
        actions=actions,
    )

    case_pass = bool(
        outcome_correctness
        and (
            route_correctness
            if strategy_name == "tiered"
            else True
        )
    )

    usage = collector.summary()

    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "strategy": strategy_name,
        "router_observed_route": router_observed_route,
        "execution_route": profile.route.value,
        "route_correctness": route_correctness,
        "model_tier": profile.model_tier.value,
        "model_name": profile.model_name,
        "context_strategy": (
            strategy.evidence_context_strategy.value
        ),
        "deterministic_symbol_first": (
            strategy.deterministic_symbol_first
        ),
        "expected_outcome": expected_outcome,
        "task_success": task_success,
        "outcome_correctness": outcome_correctness,
        "case_pass": case_pass,
        "source_coverage": source_coverage,
        "decision_context_applicable": True,
        "decision_context_source_coverage": (
            context_eval.source_coverage
        ),
        "decision_context_coverage": (
            context_eval.decision_context_coverage
        ),
        "grounded_completion": grounded_completion,
        "provenance_integrity": provenance_integrity,
        **controls,
        "termination_correctness": termination_correctness,
        "termination_reason": termination_reason,
        "step_count": state["step_count"],
        "tool_calls": sum(
            event.event_type is AgentEventType.TOOL_CALL
            for event in events
        ),
        "llm_calls": len(collector.records),
        "usage": usage,
        "estimated_cost_usd": estimate_cost_usd(
            model=profile.model_name,
            usage=usage,
        ),
        "latency_ms": latency_ms,
        "evidence_paths": actual_paths,
        "decision_context_evidence_paths": (
            final_context_paths
        ),
        "action_sequence": actions,
        "missing_context_requirement_ids": (
            context_eval.missing_requirement_ids
        ),
    }


async def run_case_pair(
    *,
    case: dict[str, Any],
    case_index: int,
    root: Path,
    router: HeuristicTaskRouter,
    policy: DefaultExecutionPolicy,
    strategy_policy: DefaultExecutionStrategyPolicy,
) -> dict[str, Any]:
    observed = router.route(case["query"])
    expected_route = case["expected_route"]
    route_correctness = observed.route.value == expected_route

    tiered_profile = policy.resolve(observed)
    tiered_strategy = strategy_policy.resolve(tiered_profile)

    async def run_tiered():
        if tiered_profile.route is ExecutionRoute.WORKFLOW:
            return await run_workflow_case(
                case=case,
                root=root,
                strategy_name="tiered",
                router_observed_route=observed.route.value,
                route_correctness=route_correctness,
            )
        return await run_agent_case(
            case=case,
            root=root,
            profile=tiered_profile,
            strategy=tiered_strategy,
            strategy_name="tiered",
            router_observed_route=observed.route.value,
            route_correctness=route_correctness,
        )

    async def run_baseline():
        if expected_route == ExecutionRoute.WORKFLOW.value:
            return await run_workflow_case(
                case=case,
                root=root,
                strategy_name="always_large",
                router_observed_route=observed.route.value,
                route_correctness=None,
            )

        baseline_profile = policy.resolve(
            forced_research_decision()
        )
        baseline_strategy = strategy_policy.resolve(
            baseline_profile
        )
        return await run_agent_case(
            case=case,
            root=root,
            profile=baseline_profile,
            strategy=baseline_strategy,
            strategy_name="always_large",
            router_observed_route=observed.route.value,
            route_correctness=None,
        )

    # Alternate arm order to reduce a fixed temporal ordering bias in latency.
    if case_index % 2 == 0:
        baseline = await run_baseline()
        tiered = await run_tiered()
    else:
        tiered = await run_tiered()
        baseline = await run_baseline()

    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "expected_route": expected_route,
        "expected_outcome": case["expected_outcome"],
        "tiered": tiered,
        "always_large": baseline,
        "delta_baseline_minus_tiered": {
            "llm_calls": (
                baseline["llm_calls"] - tiered["llm_calls"]
            ),
            "tool_calls": (
                baseline["tool_calls"] - tiered["tool_calls"]
            ),
            "total_tokens": (
                baseline["usage"]["total_tokens"]
                - tiered["usage"]["total_tokens"]
            ),
            "latency_ms": (
                baseline["latency_ms"] - tiered["latency_ms"]
            ),
            "estimated_cost_usd": (
                baseline["estimated_cost_usd"]
                - tiered["estimated_cost_usd"]
            ),
        },
    }


def summarize_arm(
    rows: list[dict[str, Any]],
    arm: str,
) -> dict[str, Any]:
    values = [row[arm] for row in rows if arm in row]
    if not values:
        return {}

    positive = [
        item for item in values
        if item["expected_outcome"] == "completed"
    ]

    def avg(field: str, items=values) -> float:
        return (
            sum(float(item[field]) for item in items) / len(items)
            if items
            else 0.0
        )

    return {
        "case_count": len(values),
        "case_pass_count": sum(
            bool(item["case_pass"]) for item in values
        ),
        "case_pass_rate": avg("case_pass"),
        "route_correct_count": (
            sum(
                item["route_correctness"] is True
                for item in values
            )
            if arm == "tiered"
            else None
        ),
        "positive_task_success_count": sum(
            bool(item["task_success"]) for item in positive
        ),
        "positive_task_success_rate": avg(
            "task_success",
            positive,
        ),
        "avg_positive_source_coverage": avg(
            "source_coverage",
            positive,
        ),
        "avg_positive_decision_context_source_coverage": avg(
            "decision_context_source_coverage",
            positive,
        ),
        "avg_positive_decision_context_coverage": avg(
            "decision_context_coverage",
            positive,
        ),
        "grounded_completion_count": sum(
            bool(item["grounded_completion"])
            for item in positive
        ),
        "consecutive_duplicate_action_cases": sum(
            item["consecutive_duplicate_action_count"] > 0
            for item in values
        ),
        "zero_evidence_loop_cases": sum(
            item["zero_evidence_action_count"] >= 2
            for item in values
        ),
        "llm_calls": sum(item["llm_calls"] for item in values),
        "total_tokens": sum(
            item["usage"]["total_tokens"]
            for item in values
        ),
        "latency_ms_sum": sum(
            item["latency_ms"] for item in values
        ),
        "estimated_cost_usd": sum(
            item["estimated_cost_usd"]
            for item in values
        ),
    }


def robustness_by_category(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if "tiered" in row and "always_large" in row:
            grouped[row["category"]].append(row)

    result: dict[str, Any] = {}

    for category, category_rows in sorted(grouped.items()):
        result[category] = {
            "case_count": len(category_rows),
            "tiered": summarize_arm(
                category_rows,
                "tiered",
            ),
            "always_large": summarize_arm(
                category_rows,
                "always_large",
            ),
        }

    return result


def symptom_counts(
    rows: list[dict[str, Any]],
    arm: str,
) -> dict[str, int]:
    """
    Counts on this balanced benchmark only.

    These are cross-workload diagnostic signals, NOT estimates of real-world
    production failure frequency.
    """
    counts = {
        "route_miss": 0,
        "source_miss_on_completed_target": 0,
        "final_context_source_miss": 0,
        "final_context_marker_miss": 0,
        "max_steps": 0,
        "no_actionable_path": 0,
        "consecutive_duplicate_action": 0,
        "two_or_more_zero_evidence_actions": 0,
        "recovery_failed": 0,
    }

    for row in rows:
        if arm not in row:
            continue
        item = row[arm]

        if item["route_correctness"] is False:
            counts["route_miss"] += 1

        if (
            item["expected_outcome"] == "completed"
            and item["source_coverage"] < 1.0
        ):
            counts["source_miss_on_completed_target"] += 1

        if (
            item["expected_outcome"] == "completed"
            and item["decision_context_source_coverage"] < 1.0
        ):
            counts["final_context_source_miss"] += 1

        if (
            item["expected_outcome"] == "completed"
            and item["decision_context_coverage"] < 1.0
        ):
            counts["final_context_marker_miss"] += 1

        if item["termination_reason"] == "max_steps":
            counts["max_steps"] += 1

        if item["termination_reason"] == "no_actionable_path":
            counts["no_actionable_path"] += 1

        if item["consecutive_duplicate_action_count"] > 0:
            counts["consecutive_duplicate_action"] += 1

        if item["zero_evidence_action_count"] >= 2:
            counts["two_or_more_zero_evidence_actions"] += 1

        recovered = item["recovered_from_wrong_first_action"]
        if recovered is False:
            counts["recovery_failed"] += 1

    return counts


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid_rows = [
        row for row in rows
        if "tiered" in row and "always_large" in row
    ]
    tiered = summarize_arm(valid_rows, "tiered")
    baseline = summarize_arm(valid_rows, "always_large")

    return {
        "requested_case_count": len(rows),
        "completed_pair_count": len(valid_rows),
        "harness_error_count": sum(
            "harness_error" in row for row in rows
        ),
        "tiered": tiered,
        "always_large": baseline,
        "savings_baseline_minus_tiered": {
            "llm_calls": (
                baseline.get("llm_calls", 0)
                - tiered.get("llm_calls", 0)
            ),
            "total_tokens": (
                baseline.get("total_tokens", 0)
                - tiered.get("total_tokens", 0)
            ),
            "latency_ms_sum": (
                baseline.get("latency_ms_sum", 0.0)
                - tiered.get("latency_ms_sum", 0.0)
            ),
            "estimated_cost_usd": (
                baseline.get("estimated_cost_usd", 0.0)
                - tiered.get("estimated_cost_usd", 0.0)
            ),
        },
        "robustness_by_category": robustness_by_category(
            valid_rows
        ),
        "balanced_benchmark_symptoms": {
            "interpretation": (
                "Diagnostic counts across a deliberately balanced benchmark; "
                "do not interpret as production failure frequencies."
            ),
            "tiered": symptom_counts(valid_rows, "tiered"),
            "always_large": symptom_counts(
                valid_rows,
                "always_large",
            ),
        },
    }


def result_envelope(
    *,
    rows: list[dict[str, Any]],
    selected_case_count: int,
    status: str,
) -> dict[str, Any]:
    return {
        "evaluation": "Day34 24-case robustness matrix",
        "status": status,
        "selected_case_count": selected_case_count,
        "comparison": "tiered_vs_always_large",
        "benchmark_design": {
            "balanced_categories": True,
            "category_case_target": 3,
            "production_frequency_estimate": False,
            "purpose": (
                "Measure robustness by workload category and identify "
                "cross-category systemic failure mechanisms."
            ),
        },
        "evaluation_corpus": {
            "excluded_dirs_added_by_eval": ["evals"],
            "reason": (
                "Prevent the benchmark fixture from being retrieved as "
                "repository evidence by SearchCodeTool."
            ),
        },
        "decision_context_metric": {
            "scope": "final semantic decision prompt evidence only",
            "task_text_included": False,
            "earlier_prompt_union": False,
        },
        "quality_boundary": {
            "final_answer_quality_evaluated": False,
            "grounded_completion_meaning": (
                "The final semantic decision saw the required authoritative "
                "source paths and required evidence markers before completion. "
                "Natural-language final synthesis quality is not evaluated."
            ),
        },
        "baseline_definition": (
            "Deterministic workflow remains deterministic in both arms. "
            "Every semantic case is forced to Pro Research Agent in Always-Large."
        ),
        "pricing_snapshot": "2026-08-19 fixed Day33 snapshot",
        "summary": summarize(rows),
        "cases": rows,
    }


async def main() -> None:
    args = parse_args()

    if not settings.deepseek_api_key.strip():
        raise RuntimeError(
            "DEEPSEEK_API_KEY is empty; Day34 robustness evaluation "
            "requires the real model."
        )

    root = Path.cwd()
    all_cases = load_cases(root)
    cases = filter_cases(
        all_cases,
        case_ids=args.case_id,
        categories=args.category,
    )

    router = HeuristicTaskRouter()
    policy = DefaultExecutionPolicy()
    strategy_policy = DefaultExecutionStrategyPolicy()

    output_dir = root / ".local/days/day34"
    output_dir.mkdir(parents=True, exist_ok=True)

    partial_path = (
        output_dir / "research_robustness_matrix.partial.json"
    )
    final_path = (
        output_dir / "research_robustness_matrix_results.json"
    )

    rows: list[dict[str, Any]] = []

    for index, case in enumerate(cases, start=1):
        print(
            f"[{index}/{len(cases)}] "
            f"{case['category']} :: {case['case_id']}",
            flush=True,
        )

        try:
            row = await run_case_pair(
                case=case,
                case_index=index,
                root=root,
                router=router,
                policy=policy,
                strategy_policy=strategy_policy,
            )
        except Exception as exc:
            row = {
                "case_id": case["case_id"],
                "category": case["category"],
                "expected_route": case["expected_route"],
                "expected_outcome": case["expected_outcome"],
                "harness_error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            }
            print(
                f"  HARNESS ERROR: {type(exc).__name__}: {exc}",
                flush=True,
            )

        rows.append(row)

        partial = result_envelope(
            rows=rows,
            selected_case_count=len(cases),
            status="partial",
        )
        partial_path.write_text(
            json.dumps(
                partial,
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    result = result_envelope(
        rows=rows,
        selected_case_count=len(cases),
        status="complete",
    )
    final_path.write_text(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print("\n=== DAY34 ROBUSTNESS SUMMARY ===")
    print(
        json.dumps(
            result["summary"],
            indent=2,
            ensure_ascii=False,
        )
    )
    print(f"\nFull result: {final_path}")
    print(f"Checkpoint: {partial_path}")


if __name__ == "__main__":
    asyncio.run(main())
