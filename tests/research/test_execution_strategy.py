from __future__ import annotations

from app.research.execution_policy import DefaultExecutionPolicy
from app.research.execution_strategy import (
    DefaultExecutionStrategyPolicy,
    EvidenceContextStrategy,
)
from app.research.context_metrics import (
    DecisionContextRequirement,
    evaluate_context_coverage,
)
from app.research.task_router import HeuristicTaskRouter


def test_light_strategy_uses_flash_focused_context_and_fast_path() -> None:
    router = HeuristicTaskRouter()
    profile_policy = DefaultExecutionPolicy()
    strategy_policy = DefaultExecutionStrategyPolicy()

    profile = profile_policy.resolve(
        router.route("How does ToolRuntime enforce timeout handling?")
    )
    strategy = strategy_policy.resolve(profile)

    assert strategy.profile.model_name == "deepseek-v4-flash"
    assert strategy.profile.max_steps == 2
    assert strategy.deterministic_symbol_first is True
    assert (
        strategy.evidence_context_strategy
        is EvidenceContextStrategy.QUERY_FOCUSED
    )


def test_research_strategy_keeps_dynamic_autonomy() -> None:
    router = HeuristicTaskRouter()
    profile_policy = DefaultExecutionPolicy()
    strategy_policy = DefaultExecutionStrategyPolicy()

    profile = profile_policy.resolve(
        router.route(
            "Compare RepoExplorer and ToolRuntime responsibilities."
        )
    )
    strategy = strategy_policy.resolve(profile)

    assert strategy.profile.model_name == "deepseek-v4-pro"
    assert strategy.deterministic_symbol_first is False


def test_context_metrics_distinguish_source_from_visible_support() -> None:
    requirements = [
        DecisionContextRequirement(
            requirement_id="timeout-boundary",
            required_markers=[
                "asyncio.wait_for",
                "tool.timeout_seconds",
            ],
        ),
        DecisionContextRequirement(
            requirement_id="timeout-result",
            required_markers=["ToolErrorCode.TIMEOUT"],
        ),
    ]

    result = evaluate_context_coverage(
        expected_source_paths=["app/harness/tool_runtime.py"],
        actual_source_paths=["app/harness/tool_runtime.py"],
        visible_contexts=[
            "asyncio.wait_for(... timeout=tool.timeout_seconds)"
        ],
        requirements=requirements,
        completed=True,
    )

    assert result.source_coverage == 1.0
    assert result.decision_context_coverage == 0.5
    assert result.grounded_completion is False
    assert result.missing_requirement_ids == ["timeout-result"]
