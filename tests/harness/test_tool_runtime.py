import asyncio

import pytest
from pydantic import BaseModel

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
