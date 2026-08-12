from __future__ import annotations

import asyncio
import time
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.harness.agent_event import (
    AgentEvent,
    AgentEventSink,
    AgentEventType,
    record_event_safely,
)


class ToolRiskLevel(StrEnum):
    READ = "read"
    COMPUTE = "compute"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class ToolErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    EXECUTION_ERROR = "execution_error"
    INVALID_OUTPUT = "invalid_output"


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    data: dict[str, Any] | None = None
    error_code: ToolErrorCode | None = None
    latency_ms: float
    truncated: bool = False
    trace_metadata: dict[str, Any] = Field(default_factory=dict)


class ToolContract(Protocol):
    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    risk_level: ToolRiskLevel
    timeout_seconds: float
    max_retries: int

    async def execute(self, tool_input: BaseModel) -> BaseModel:
        ...


class ToolRuntime:
    """Execute tools through one validated permission boundary."""

    def __init__(
        self,
        *,
        allowed_risk_levels: frozenset[ToolRiskLevel] = frozenset(
            {
                ToolRiskLevel.READ,
                ToolRiskLevel.COMPUTE,
            }
        ),
        event_sink: AgentEventSink | None = None,
    ) -> None:
        self._allowed_risk_levels = allowed_risk_levels
        self._event_sink = event_sink

    async def invoke(
        self,
        *,
        tool: ToolContract,
        arguments: dict[str, Any],
        trace_metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        started = time.perf_counter()
        metadata = dict(trace_metadata or {})
        metadata["tool_name"] = tool.name

        if self._event_sink is not None:
            metadata["trace_id"] = str(
                metadata.get("trace_id") or uuid4().hex
            )

        call_event = self._emit_tool_call(
            tool_name=tool.name,
            arguments=arguments,
            trace_metadata=metadata,
        )

        if tool.risk_level not in self._allowed_risk_levels:
            return self._finish(
                result=self._failure(
                    started=started,
                    error_code=ToolErrorCode.PERMISSION_DENIED,
                    trace_metadata=metadata,
                ),
                call_event=call_event,
                tool_name=tool.name,
            )

        try:
            tool_input = tool.input_schema.model_validate(arguments)
        except ValidationError:
            return self._finish(
                result=self._failure(
                    started=started,
                    error_code=ToolErrorCode.INVALID_INPUT,
                    trace_metadata=metadata,
                ),
                call_event=call_event,
                tool_name=tool.name,
            )

        try:
            raw_output = await asyncio.wait_for(
                tool.execute(tool_input),
                timeout=tool.timeout_seconds,
            )
        except TimeoutError:
            return self._finish(
                result=self._failure(
                    started=started,
                    error_code=ToolErrorCode.TIMEOUT,
                    trace_metadata=metadata,
                ),
                call_event=call_event,
                tool_name=tool.name,
            )
        except Exception:
            return self._finish(
                result=self._failure(
                    started=started,
                    error_code=ToolErrorCode.EXECUTION_ERROR,
                    trace_metadata=metadata,
                ),
                call_event=call_event,
                tool_name=tool.name,
            )

        try:
            output = tool.output_schema.model_validate(raw_output)
        except ValidationError:
            return self._finish(
                result=self._failure(
                    started=started,
                    error_code=ToolErrorCode.INVALID_OUTPUT,
                    trace_metadata=metadata,
                ),
                call_event=call_event,
                tool_name=tool.name,
            )

        return self._finish(
            result=ToolResult(
                ok=True,
                data=output.model_dump(),
                truncated=bool(getattr(output, "truncated", False)),
                latency_ms=self._elapsed_ms(started),
                trace_metadata=metadata,
            ),
            call_event=call_event,
            tool_name=tool.name,
        )

    def _emit_tool_call(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        trace_metadata: dict[str, Any],
    ) -> AgentEvent | None:
        trace_id = trace_metadata.get("trace_id")
        if self._event_sink is None or not trace_id:
            return None

        event = AgentEvent(
            trace_id=str(trace_id),
            parent_event_id=self._metadata_parent_event_id(trace_metadata),
            event_type=AgentEventType.TOOL_CALL,
            component="tool_runtime",
            tool_name=tool_name,
            input_summary={"argument_keys": sorted(arguments)},
            trace_metadata=dict(trace_metadata),
        )
        if not record_event_safely(self._event_sink, event):
            return None
        return event

    def _finish(
        self,
        *,
        result: ToolResult,
        call_event: AgentEvent | None,
        tool_name: str,
    ) -> ToolResult:
        trace_id = result.trace_metadata.get("trace_id")
        if self._event_sink is None or not trace_id:
            return result

        output_summary: dict[str, Any] = {
            "ok": result.ok,
            "has_data": result.data is not None,
            "truncated": result.truncated,
        }
        if result.data is not None:
            output_summary["data_keys"] = sorted(result.data)

        event = AgentEvent(
            trace_id=str(trace_id),
            parent_event_id=(
                call_event.event_id
                if call_event is not None
                else self._metadata_parent_event_id(result.trace_metadata)
            ),
            event_type=AgentEventType.TOOL_RESULT,
            component="tool_runtime",
            tool_name=tool_name,
            output_summary=output_summary,
            latency_ms=result.latency_ms,
            error_code=(
                result.error_code.value
                if result.error_code is not None
                else None
            ),
            trace_metadata=dict(result.trace_metadata),
        )
        record_event_safely(self._event_sink, event)
        return result

    @staticmethod
    def _metadata_parent_event_id(
        trace_metadata: dict[str, Any],
    ) -> str | None:
        value = trace_metadata.get("parent_event_id")
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _failure(
        *,
        started: float,
        error_code: ToolErrorCode,
        trace_metadata: dict[str, Any],
    ) -> ToolResult:
        return ToolResult(
            ok=False,
            error_code=error_code,
            latency_ms=ToolRuntime._elapsed_ms(started),
            trace_metadata=trace_metadata,
        )

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return (time.perf_counter() - started) * 1000
