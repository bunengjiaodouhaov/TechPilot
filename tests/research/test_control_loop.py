from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from app.harness.agent_event import AgentEventType, InMemoryAgentEventSink
from app.harness.tool_registry import ToolRegistry
from app.harness.tool_runtime import (
    ToolRiskLevel,
    ToolRuntime,
)
from app.research.contracts import (
    ResearchAction,
    ResearchState,
    ResearchStep,
    TerminationReason,
    VerificationResult,
)
from app.research.graph import build_research_graph


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str


class SearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_path: str


class ReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


class ReadOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    content: str
    authoritative: bool


class EmptyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


class FakeSearchTool:
    name = "fake_search_source"
    description = "Locate a candidate source."
    input_schema = SearchInput
    output_schema = SearchOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 1.0
    max_retries = 0

    async def execute(self, tool_input: SearchInput) -> SearchOutput:
        return SearchOutput(candidate_path="docs/checkpoint.md")


class FakeReadTool:
    name = "fake_read_source"
    description = "Materialize authoritative source content."
    input_schema = ReadInput
    output_schema = ReadOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 1.0
    max_retries = 0

    async def execute(self, tool_input: ReadInput) -> ReadOutput:
        return ReadOutput(
            path=tool_input.path,
            content="Checkpoint persistence is implemented by a saver abstraction.",
            authoritative=True,
        )


class DeniedWriteTool:
    name = "fake_write"
    description = "A write operation that must be denied."
    input_schema = SearchInput
    output_schema = EmptyOutput
    risk_level = ToolRiskLevel.WRITE
    timeout_seconds = 1.0
    max_retries = 0

    async def execute(self, tool_input: SearchInput) -> EmptyOutput:
        raise AssertionError("permission boundary should prevent execution")


class TimeoutTool:
    name = "fake_timeout"
    description = "Always times out."
    input_schema = SearchInput
    output_schema = EmptyOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 1.0
    max_retries = 0

    async def execute(self, tool_input: SearchInput) -> EmptyOutput:
        raise TimeoutError


class SingleStepPlanner:
    def plan(self, normalized_task: str) -> list[ResearchStep]:
        return [
            ResearchStep(
                objective=normalized_task,
                source_requirement="authoritative source",
            )
        ]


class SearchThenReadSelector:
    def select_action(self, state: ResearchState) -> ResearchAction | None:
        last = state.get("last_tool_result")

        if last is None:
            return ResearchAction(
                tool_name="fake_search_source",
                arguments={"query": state["normalized_task"]},
                reason="Locate a candidate source for the research objective.",
            )

        data = last.data or {}
        candidate_path = data.get("candidate_path")
        if candidate_path:
            return ResearchAction(
                tool_name="fake_read_source",
                arguments={"path": candidate_path},
                reason="Materialize the candidate into authoritative source content.",
            )

        return None


class AlwaysDeniedSelector:
    def select_action(self, state: ResearchState) -> ResearchAction | None:
        return ResearchAction(
            tool_name="fake_write",
            arguments={"query": state["normalized_task"]},
            reason="Exercise the runtime permission boundary.",
        )


class AlwaysTimeoutSelector:
    def select_action(self, state: ResearchState) -> ResearchAction | None:
        return ResearchAction(
            tool_name="fake_timeout",
            arguments={"query": state["normalized_task"]},
            reason="Retry only the explicitly retryable timeout failure.",
        )


class AlwaysSearchSelector:
    def select_action(self, state: ResearchState) -> ResearchAction | None:
        return ResearchAction(
            tool_name="fake_search_source",
            arguments={"query": state["normalized_task"]},
            reason="Keep searching while evidence remains insufficient.",
        )


class AuthoritativeVerifier:
    def verify(self, state: ResearchState) -> VerificationResult:
        last = state.get("last_tool_result")
        data: dict[str, Any] = (last.data if last is not None else None) or {}

        if last is not None and last.ok and data.get("authoritative") is True:
            return VerificationResult(
                sufficient=True,
                reason="Authoritative source content now covers the objective.",
            )

        return VerificationResult(
            sufficient=False,
            reason="Only a candidate or failed tool result exists.",
            unresolved_questions=["Authoritative content is still missing."],
        )


class StaticFinalizer:
    def finalize(self, state: ResearchState) -> str:
        if state["termination_reason"] is TerminationReason.COMPLETED:
            return "Research completed with authoritative source material."
        return f"Research incomplete: {state['termination_reason'].value}"


def _registry(*tools: object) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


@pytest.mark.asyncio
async def test_bounded_loop_searches_then_materializes_source() -> None:
    sink = InMemoryAgentEventSink()
    runtime = ToolRuntime(event_sink=sink)
    graph = build_research_graph(
        registry=_registry(FakeSearchTool(), FakeReadTool()),
        runtime=runtime,
        planner=SingleStepPlanner(),
        action_selector=SearchThenReadSelector(),
        verifier=AuthoritativeVerifier(),
        finalizer=StaticFinalizer(),
    )

    result = await graph.ainvoke(
        {
            "query": "How is checkpoint persistence implemented?",
            "max_steps": 4,
        },
        context={"trace_id": "day31-control-loop"},
    )

    assert result["termination_reason"] is TerminationReason.COMPLETED
    assert result["incomplete"] is False
    assert result["step_count"] == 2

    events = sink.events_for_trace("day31-control-loop")
    assert [event.event_type for event in events] == [
        AgentEventType.TOOL_CALL,
        AgentEventType.TOOL_RESULT,
        AgentEventType.TOOL_CALL,
        AgentEventType.TOOL_RESULT,
    ]
    assert [event.tool_name for event in events] == [
        "fake_search_source",
        "fake_search_source",
        "fake_read_source",
        "fake_read_source",
    ]
    call_events = [
        event for event in events if event.event_type is AgentEventType.TOOL_CALL
    ]
    assert call_events[0].trace_metadata["decision_reason"].startswith("Locate")
    assert call_events[1].trace_metadata["decision_reason"].startswith("Materialize")


@pytest.mark.asyncio
async def test_permission_denied_is_permanent_failure_without_retry() -> None:
    sink = InMemoryAgentEventSink()
    runtime = ToolRuntime(event_sink=sink)
    graph = build_research_graph(
        registry=_registry(DeniedWriteTool()),
        runtime=runtime,
        planner=SingleStepPlanner(),
        action_selector=AlwaysDeniedSelector(),
        verifier=AuthoritativeVerifier(),
        finalizer=StaticFinalizer(),
    )

    result = await graph.ainvoke(
        {"query": "Attempt a denied operation.", "max_steps": 5},
        context={"trace_id": "day31-permission"},
    )

    assert result["termination_reason"] is TerminationReason.PERMANENT_FAILURE
    assert result["incomplete"] is True
    assert result["step_count"] == 1

    events = sink.events_for_trace("day31-permission")
    assert len(events) == 2
    assert events[-1].error_code == "permission_denied"


@pytest.mark.asyncio
async def test_timeout_retries_only_until_retry_budget_is_exhausted() -> None:
    sink = InMemoryAgentEventSink()
    runtime = ToolRuntime(event_sink=sink)
    graph = build_research_graph(
        registry=_registry(TimeoutTool()),
        runtime=runtime,
        planner=SingleStepPlanner(),
        action_selector=AlwaysTimeoutSelector(),
        verifier=AuthoritativeVerifier(),
        finalizer=StaticFinalizer(),
    )

    result = await graph.ainvoke(
        {
            "query": "Research through a transient source.",
            "max_steps": 10,
            "max_retries": 1,
        },
        context={"trace_id": "day31-timeout"},
    )

    assert result["termination_reason"] is TerminationReason.RETRY_EXHAUSTED
    assert result["incomplete"] is True
    assert result["step_count"] == 2
    assert result["retry_count"] == 2


@pytest.mark.asyncio
async def test_insufficient_evidence_stops_at_max_steps() -> None:
    sink = InMemoryAgentEventSink()
    runtime = ToolRuntime(event_sink=sink)
    graph = build_research_graph(
        registry=_registry(FakeSearchTool()),
        runtime=runtime,
        planner=SingleStepPlanner(),
        action_selector=AlwaysSearchSelector(),
        verifier=AuthoritativeVerifier(),
        finalizer=StaticFinalizer(),
    )

    result = await graph.ainvoke(
        {
            "query": "Research a question whose evidence never materializes.",
            "max_steps": 2,
        },
        context={"trace_id": "day31-max-steps"},
    )

    assert result["termination_reason"] is TerminationReason.MAX_STEPS
    assert result["incomplete"] is True
    assert result["step_count"] == 2
