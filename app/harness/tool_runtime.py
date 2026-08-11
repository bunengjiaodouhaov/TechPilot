from __future__ import annotations

import asyncio
import time
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError


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
    ) -> None:
        self._allowed_risk_levels = allowed_risk_levels

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

        if tool.risk_level not in self._allowed_risk_levels:
            return self._failure(
                started=started,
                error_code=ToolErrorCode.PERMISSION_DENIED,
                trace_metadata=metadata,
            )

        try:
            tool_input = tool.input_schema.model_validate(arguments)
        except ValidationError:
            return self._failure(
                started=started,
                error_code=ToolErrorCode.INVALID_INPUT,
                trace_metadata=metadata,
            )

        try:
            raw_output = await asyncio.wait_for(
                tool.execute(tool_input),
                timeout=tool.timeout_seconds,
            )
        except TimeoutError:
            return self._failure(
                started=started,
                error_code=ToolErrorCode.TIMEOUT,
                trace_metadata=metadata,
            )
        except Exception:
            return self._failure(
                started=started,
                error_code=ToolErrorCode.EXECUTION_ERROR,
                trace_metadata=metadata,
            )

        try:
            output = tool.output_schema.model_validate(raw_output)
        except ValidationError:
            return self._failure(
                started=started,
                error_code=ToolErrorCode.INVALID_OUTPUT,
                trace_metadata=metadata,
            )

        return ToolResult(
            ok=True,
            data=output.model_dump(),
            truncated=bool(getattr(output, "truncated", False)),
            latency_ms=self._elapsed_ms(started),
            trace_metadata=metadata,
        )

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
