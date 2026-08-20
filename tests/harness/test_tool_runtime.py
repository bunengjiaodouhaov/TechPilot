import asyncio

import pytest
from pydantic import BaseModel
from app.harness.agent_event import (
    AgentEventType,
    InMemoryAgentEventSink,
)

from app.harness.tool_runtime import (
    ToolErrorCode,
    ToolRiskLevel,
    ToolRuntime,
)


class EchoInput(BaseModel):
    value: str


class EchoOutput(BaseModel):
    echoed: str


class FakeTool:
    name = "echo"
    description = "Echo validated input."
    input_schema = EchoInput
    output_schema = EchoOutput
    risk_level = ToolRiskLevel.READ
    timeout_seconds = 0.1
    max_retries = 0

    async def execute(self, tool_input: EchoInput) -> EchoOutput:
        return EchoOutput(echoed=tool_input.value)


@pytest.mark.asyncio
async def test_runtime_validates_and_executes_tool() -> None:
    result = await ToolRuntime().invoke(
        tool=FakeTool(),
        arguments={"value": "hello"},
        trace_metadata={"trace_id": "trace-1"},
    )

    assert result.ok is True
    assert result.data == {"echoed": "hello"}
    assert result.error_code is None
    assert result.latency_ms >= 0
    assert result.trace_metadata == {
        "trace_id": "trace-1",
        "tool_name": "echo",
    }


@pytest.mark.asyncio
async def test_runtime_rejects_invalid_input() -> None:
    result = await ToolRuntime().invoke(
        tool=FakeTool(),
        arguments={},
    )

    assert result.ok is False
    assert result.error_code == ToolErrorCode.INVALID_INPUT


@pytest.mark.asyncio
async def test_runtime_enforces_permission_boundary() -> None:
    tool = FakeTool()
    tool.risk_level = ToolRiskLevel.WRITE

    result = await ToolRuntime().invoke(
        tool=tool,
        arguments={"value": "hello"},
    )

    assert result.ok is False
    assert result.error_code == ToolErrorCode.PERMISSION_DENIED


@pytest.mark.asyncio
async def test_runtime_returns_structured_timeout() -> None:
    class SlowTool(FakeTool):
        timeout_seconds = 0.001

        async def execute(self, tool_input: EchoInput) -> EchoOutput:
            await asyncio.sleep(0.05)
            return EchoOutput(echoed=tool_input.value)

    result = await ToolRuntime().invoke(
        tool=SlowTool(),
        arguments={"value": "hello"},
    )

    assert result.ok is False
    assert result.error_code == ToolErrorCode.TIMEOUT


@pytest.mark.asyncio
async def test_runtime_returns_structured_execution_error() -> None:
    class BrokenTool(FakeTool):
        async def execute(self, tool_input: EchoInput) -> EchoOutput:
            raise RuntimeError("boom")

    result = await ToolRuntime().invoke(
        tool=BrokenTool(),
        arguments={"value": "hello"},
    )

    assert result.ok is False
    assert result.error_code == ToolErrorCode.EXECUTION_ERROR


@pytest.mark.asyncio
async def test_runtime_rejects_invalid_output() -> None:
    class BadOutputTool(FakeTool):
        async def execute(self, tool_input: EchoInput) -> dict[str, str]:
            return {"wrong": tool_input.value}

    result = await ToolRuntime().invoke(
        tool=BadOutputTool(),
        arguments={"value": "hello"},
    )

    assert result.ok is False
    assert result.error_code == ToolErrorCode.INVALID_OUTPUT


class TruncatedEchoOutput(BaseModel):
    echoed: str
    truncated: bool


@pytest.mark.asyncio
async def test_runtime_propagates_tool_level_truncation() -> None:
    class TruncatedTool(FakeTool):
        output_schema = TruncatedEchoOutput

        async def execute(
            self,
            tool_input: EchoInput,
        ) -> TruncatedEchoOutput:
            return TruncatedEchoOutput(
                echoed=tool_input.value,
                truncated=True,
            )

    result = await ToolRuntime().invoke(
        tool=TruncatedTool(),
        arguments={"value": "hello"},
    )

    assert result.ok is True
    assert result.data == {
        "echoed": "hello",
        "truncated": True,
    }
    assert result.truncated is True

@pytest.mark.asyncio
async def test_runtime_emits_tool_call_and_result_events() -> None:
    sink = InMemoryAgentEventSink()

    result = await ToolRuntime(event_sink=sink).invoke(
        tool=FakeTool(),
        arguments={"value": "secret-value"},
        trace_metadata={"trace_id": "trace-events", "git_sha": "abc123"},
    )

    assert result.ok is True
    assert [event.event_type for event in sink.events] == [
        AgentEventType.TOOL_CALL,
        AgentEventType.TOOL_RESULT,
    ]

    call_event, result_event = sink.events
    assert call_event.trace_id == "trace-events"
    assert result_event.trace_id == "trace-events"
    assert result_event.parent_event_id == call_event.event_id
    assert call_event.tool_name == "echo"
    assert call_event.input_summary == {"argument_keys": ["value"]}
    assert "secret-value" not in str(call_event.model_dump())
    assert result_event.output_summary == {
        "ok": True,
        "has_data": True,
        "truncated": False,
        "data_keys": ["echoed"],
    }
    assert result_event.error_code is None
    assert result_event.latency_ms is not None
    assert result_event.trace_metadata["git_sha"] == "abc123"


@pytest.mark.asyncio
async def test_runtime_tracing_failure_does_not_change_tool_result() -> None:
    class FailingSink:
        def record(self, event: object) -> None:
            raise RuntimeError("trace backend unavailable")

    result = await ToolRuntime(event_sink=FailingSink()).invoke(
        tool=FakeTool(),
        arguments={"value": "hello"},
        trace_metadata={"trace_id": "trace-failure"},
    )

    assert result.ok is True
    assert result.data == {"echoed": "hello"}
