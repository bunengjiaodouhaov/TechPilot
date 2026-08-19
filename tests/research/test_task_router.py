from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.research.task_router import (
    ExecutionRoute,
    HeuristicTaskRouter,
    ModelTier,
)


@pytest.mark.parametrize(
    ("query", "route", "tier"),
    [
        (
            "Read file app/harness/tool_runtime.py.",
            ExecutionRoute.WORKFLOW,
            ModelTier.NONE,
        ),
        (
            "How does ToolRuntime enforce timeout handling?",
            ExecutionRoute.LIGHT_AGENT,
            ModelTier.MEDIUM,
        ),
        (
            "Explain both RepoExplorer and ToolRuntime mechanisms.",
            ExecutionRoute.RESEARCH_AGENT,
            ModelTier.LARGE,
        ),
    ],
)
def test_router_core_levels(
    query: str,
    route: ExecutionRoute,
    tier: ModelTier,
) -> None:
    decision = HeuristicTaskRouter().route(query)

    assert decision.route is route
    assert decision.model_tier is tier
    assert decision.reason
    assert decision.signals


def test_router_eval_fixture_has_balanced_12_cases() -> None:
    cases = json.loads(
        Path("evals/research/routing_cases.json").read_text(
            encoding="utf-8"
        )
    )

    assert len(cases) == 12
    counts = {}
    for case in cases:
        counts[case["expected_route"]] = (
            counts.get(case["expected_route"], 0) + 1
        )

    assert counts == {
        "workflow": 4,
        "light_agent": 4,
        "research_agent": 4,
    }


def test_empty_query_is_rejected() -> None:
    with pytest.raises(ValueError):
        HeuristicTaskRouter().route("   ")
