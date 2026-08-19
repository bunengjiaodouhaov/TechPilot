from __future__ import annotations

from typing import Any, Protocol

from app.harness.tool_registry import ToolNotFoundError, ToolRegistry
from app.harness.tool_runtime import ToolErrorCode, ToolResult, ToolRuntime
from app.repository.repo_explorer import RepoExploreRequest, RepoExplorer
from app.research.contracts import (
    ResearchAction,
    ResearchState,
    TerminationReason,
)


RETRYABLE_TOOL_ERRORS = frozenset({ToolErrorCode.TIMEOUT})


class ResearchActionExecutor(Protocol):
    async def execute(
        self,
        *,
        action: ResearchAction,
        state: ResearchState,
        trace_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class ToolRuntimeActionExecutor:
    """Execute a primitive tool through ToolRegistry + ToolRuntime."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        runtime: ToolRuntime,
    ) -> None:
        self._registry = registry
        self._runtime = runtime

    async def execute(
        self,
        *,
        action: ResearchAction,
        state: ResearchState,
        trace_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            tool = self._registry.get(action.tool_name)
        except ToolNotFoundError:
            return {
                "termination_reason": TerminationReason.NO_ACTIONABLE_PATH,
            }

        result = await self._runtime.invoke(
            tool=tool,
            arguments=action.arguments,
            trace_metadata=trace_metadata,
        )

        previous_retry_count = state.get("retry_count", 0)
        if result.ok:
            retry_count = 0
        elif result.error_code in RETRYABLE_TOOL_ERRORS:
            retry_count = previous_retry_count + 1
        else:
            retry_count = previous_retry_count

        return {
            "last_tool_result": result,
            "step_count": state.get("step_count", 0) + 1,
            "retry_count": retry_count,
        }


class RepoExplorerActionExecutor:
    """
    Execute the composite repository-research capability.

    RepoExplorer itself materializes candidates through read_file and sends
    every underlying repository tool call through the existing ToolRuntime.
    """

    capability_name = "repo_explore"

    def __init__(self, *, explorer: RepoExplorer) -> None:
        self._explorer = explorer

    async def execute(
        self,
        *,
        action: ResearchAction,
        state: ResearchState,
        trace_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        if action.tool_name != self.capability_name:
            return {
                "termination_reason": TerminationReason.NO_ACTIONABLE_PATH,
            }

        try:
            request = RepoExploreRequest.model_validate(action.arguments)
        except Exception:
            return {
                "termination_reason": TerminationReason.PERMANENT_FAILURE,
            }

        pack = await self._explorer.explore(
            request,
            trace_metadata=trace_metadata,
        )

        return {
            "last_tool_result": None,
            "evidence_pack": pack,
            "step_count": state.get("step_count", 0) + 1,
            "retry_count": 0,
        }
