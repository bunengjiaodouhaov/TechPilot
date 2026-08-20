from __future__ import annotations

from typing import Any

import pytest

from app.research.contracts import (
    DecisionFailureCode,
    ResearchAction,
    TerminationReason,
)
from app.research.decision_llm import ResearchDecisionProviderError
from app.research.unified_agent import (
    UnifiedDecisionKind,
    UnifiedResearchDecision,
    build_unified_research_graph,
)


class NoopExecutor:
    async def execute(
        self,
        *,
        action: ResearchAction,
        state: dict,
        trace_metadata: dict[str, Any],
    ) -> dict:
        raise AssertionError("provider retry must not execute an ACT")


class Finalizer:
    def finalize(self, state: dict) -> str:
        return state["termination_reason"].value


class FailOnceThenStop:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, state: dict) -> UnifiedResearchDecision:
        self.calls += 1
        if self.calls == 1:
            raise ResearchDecisionProviderError(
                "injected timeout",
                code=DecisionFailureCode.TIMEOUT,
                retryable=True,
            )
        return UnifiedResearchDecision(
            kind=UnifiedDecisionKind.NO_ACTIONABLE_PATH,
            reason="Stop after retry succeeds.",
            unresolved_questions=["control-test stop"],
        )


class AlwaysRetryableFailure:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, state: dict) -> UnifiedResearchDecision:
        self.calls += 1
        raise ResearchDecisionProviderError(
            "injected 503",
            code=DecisionFailureCode.UPSTREAM_ERROR,
            retryable=True,
            status_code=503,
        )


class PermanentFailure:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, state: dict) -> UnifiedResearchDecision:
        self.calls += 1
        raise ResearchDecisionProviderError(
            "injected auth failure",
            code=DecisionFailureCode.AUTH_ERROR,
            retryable=False,
            status_code=401,
        )


@pytest.mark.asyncio
async def test_retryable_provider_failure_retries_without_consuming_step() -> None:
    reasoner = FailOnceThenStop()
    graph = build_unified_research_graph(
        executor=NoopExecutor(),
        reasoner=reasoner,
        finalizer=Finalizer(),
    )

    result = await graph.ainvoke(
        {
            "query": "provider retry control",
            "max_steps": 3,
            "max_retries": 1,
            "max_decision_retries": 1,
        }
    )

    assert reasoner.calls == 2
    assert result["step_count"] == 0
    assert result["decision_retry_count"] == 0
    assert result["decision_failure"] is None
    assert result["termination_reason"] is TerminationReason.NO_ACTIONABLE_PATH


@pytest.mark.asyncio
async def test_retryable_provider_failure_stops_at_decision_retry_budget() -> None:
    reasoner = AlwaysRetryableFailure()
    graph = build_unified_research_graph(
        executor=NoopExecutor(),
        reasoner=reasoner,
        finalizer=Finalizer(),
    )

    result = await graph.ainvoke(
        {
            "query": "provider retry exhaustion",
            "max_steps": 3,
            "max_retries": 9,
            "max_decision_retries": 1,
        }
    )

    assert reasoner.calls == 2
    assert result["step_count"] == 0
    assert result["decision_retry_count"] == 2
    assert result["decision_failure"].code is DecisionFailureCode.UPSTREAM_ERROR
    assert result["decision_failure"].retryable is True
    assert result["termination_reason"] is TerminationReason.RETRY_EXHAUSTED


@pytest.mark.asyncio
async def test_permanent_provider_failure_does_not_retry() -> None:
    reasoner = PermanentFailure()
    graph = build_unified_research_graph(
        executor=NoopExecutor(),
        reasoner=reasoner,
        finalizer=Finalizer(),
    )

    result = await graph.ainvoke(
        {
            "query": "provider permanent failure",
            "max_steps": 3,
            "max_decision_retries": 5,
        }
    )

    assert reasoner.calls == 1
    assert result["step_count"] == 0
    assert result["decision_retry_count"] == 1
    assert result["decision_failure"].code is DecisionFailureCode.AUTH_ERROR
    assert result["decision_failure"].retryable is False
    assert result["termination_reason"] is TerminationReason.PERMANENT_FAILURE
