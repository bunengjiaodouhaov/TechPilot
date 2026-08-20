from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentEventType(StrEnum):
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    EVIDENCE_HANDOFF = "evidence_handoff"


class AgentEvent(BaseModel):
    """Lightweight structured trace event, separate from business state."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    trace_id: str
    parent_event_id: str | None = None
    event_type: AgentEventType
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    component: str
    tool_name: str | None = None
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float | None = Field(default=None, ge=0)
    error_code: str | None = None
    trace_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_id", "trace_id", "component")
    @classmethod
    def required_text_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized


class AgentEventSink(Protocol):
    def record(self, event: AgentEvent) -> None:
        ...


class InMemoryAgentEventSink:
    """Small test/demo sink; persistence remains a later concern."""

    def __init__(self) -> None:
        self._events: list[AgentEvent] = []

    def record(self, event: AgentEvent) -> None:
        self._events.append(event)

    @property
    def events(self) -> tuple[AgentEvent, ...]:
        return tuple(self._events)

    def events_for_trace(self, trace_id: str) -> tuple[AgentEvent, ...]:
        return tuple(
            event for event in self._events if event.trace_id == trace_id
        )


def record_event_safely(
    sink: AgentEventSink | None,
    event: AgentEvent,
) -> bool:
    """Tracing is best-effort and must not change the business outcome."""

    if sink is None:
        return False

    try:
        sink.record(event)
    except Exception:
        return False

    return True
