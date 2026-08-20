from __future__ import annotations

from typing import Any

import pytest

from app.research.contracts import (
    DecisionFailureCode,
    ResearchAction,
    TerminationReason,
)
from app.research.unified_agent import (
    UnifiedResearchReasoner,
    build_unified_research_graph,
)


class AlwaysInvalidDecisionProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict:
        self.calls += 1
        return {}


class NeverExecutor:
    async def execute(
        self,
        *,
        action: ResearchAction,
        state: dict,
        trace_metadata: dict[str, Any],
    ) -> dict:
        raise AssertionError(
            "invalid semantic decisions must never execute an ACT"
        )


class Finalizer:
    def finalize(self, state: dict) -> str:
        return state["termination_reason"].value


@pytest.mark.asyncio
async def test_bounded_repair_exhaustion_becomes_permanent_failure() -> None:
    provider = AlwaysInvalidDecisionProvider()
    reasoner = UnifiedResearchReasoner(
        provider=provider,
        capabilities={
            "repo_explore": "read-only repository research",
        },
        max_repairs=1,
    )
    graph = build_unified_research_graph(
        executor=NeverExecutor(),
        reasoner=reasoner,
        finalizer=Finalizer(),
    )

    result = await graph.ainvoke(
        {
            "query": "find registry behavior",
            "max_steps": 5,
            "max_retries": 2,
            "max_decision_retries": 2,
        }
    )

    assert provider.calls == 2
    assert result["step_count"] == 0
    assert result["termination_reason"] is TerminationReason.PERMANENT_FAILURE
    assert result["decision_retry_count"] == 1
    assert result["decision_failure"].code is DecisionFailureCode.INVALID_RESPONSE
    assert result["decision_failure"].retryable is False
