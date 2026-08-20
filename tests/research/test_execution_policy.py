from __future__ import annotations

import pytest

from app.research.execution_policy import DefaultExecutionPolicy
from app.research.task_router import (
    ExecutionRoute,
    HeuristicTaskRouter,
    ModelTier,
)


@pytest.mark.parametrize(
    ("query", "expected_route", "expected_model", "expected_steps"),
    [
        (
            "Read file app/harness/tool_runtime.py.",
            ExecutionRoute.WORKFLOW,
            None,
            0,
        ),
        (
            "How does ToolRuntime enforce timeout handling?",
            ExecutionRoute.LIGHT_AGENT,
            "deepseek-v4-flash",
            2,
        ),
        (
            (
                "Explain both RepoExplorer evidence materialization and "
                "ToolRuntime permission handling."
            ),
            ExecutionRoute.RESEARCH_AGENT,
            "deepseek-v4-pro",
            5,
        ),
    ],
)
def test_route_resolves_to_execution_profile(
    query: str,
    expected_route: ExecutionRoute,
    expected_model: str | None,
    expected_steps: int,
) -> None:
    decision = HeuristicTaskRouter().route(query)
    profile = DefaultExecutionPolicy().resolve(decision)

    assert profile.route is expected_route
    assert profile.model_name == expected_model
    assert profile.max_steps == expected_steps


def test_light_profile_has_smaller_budget_than_research() -> None:
    router = HeuristicTaskRouter()
    policy = DefaultExecutionPolicy()

    light = policy.resolve(
        router.route("How does ToolRuntime enforce timeout handling?")
    )
    research = policy.resolve(
        router.route(
            "Compare RepoExplorer and ToolRuntime responsibilities."
        )
    )

    assert light.model_tier is ModelTier.MEDIUM
    assert research.model_tier is ModelTier.LARGE
    assert light.max_steps < research.max_steps
    assert light.max_evidence_items < research.max_evidence_items
    assert (
        light.evidence_snippet_characters
        < research.evidence_snippet_characters
    )
    assert (
        light.max_decision_output_tokens
        < research.max_decision_output_tokens
    )
