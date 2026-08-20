import pytest
from pydantic import ValidationError

from app.harness.agent_event import (
    AgentEvent,
    AgentEventType,
    InMemoryAgentEventSink,
)


def test_in_memory_sink_keeps_trace_events_separate() -> None:
    sink = InMemoryAgentEventSink()
    first = AgentEvent(
        trace_id="trace-1",
        event_type=AgentEventType.TOOL_CALL,
        component="tool_runtime",
    )
    second = AgentEvent(
        trace_id="trace-2",
        event_type=AgentEventType.EVIDENCE_HANDOFF,
        component="repo_explorer",
    )

    sink.record(first)
    sink.record(second)

    assert sink.events == (first, second)
    assert sink.events_for_trace("trace-1") == (first,)


def test_agent_event_rejects_blank_trace_id() -> None:
    with pytest.raises(ValidationError):
        AgentEvent(
            trace_id=" ",
            event_type=AgentEventType.TOOL_CALL,
            component="tool_runtime",
        )
