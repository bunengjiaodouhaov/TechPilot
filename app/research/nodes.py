from __future__ import annotations

import inspect
from typing import Awaitable, Protocol

from langgraph.runtime import Runtime

from app.harness.tool_runtime import ToolErrorCode, ToolResult
from app.research.contracts import (
    ResearchAction,
    ResearchContext,
    ResearchState,
    ResearchStep,
    TerminationReason,
    VerificationResult,
)
from app.research.execution import ResearchActionExecutor


PERMANENT_TOOL_ERRORS = frozenset(
    {
        ToolErrorCode.INVALID_INPUT,
        ToolErrorCode.PERMISSION_DENIED,
        ToolErrorCode.EXECUTION_ERROR,
        ToolErrorCode.INVALID_OUTPUT,
    }
)


class ResearchPlanner(Protocol):
    def plan(
        self,
        normalized_task: str,
    ) -> list[ResearchStep] | Awaitable[list[ResearchStep]]:
        ...


class ResearchActionSelector(Protocol):
    def select_action(
        self,
        state: ResearchState,
    ) -> ResearchAction | None | Awaitable[ResearchAction | None]:
        ...


class ResearchVerifier(Protocol):
    def verify(
        self,
        state: ResearchState,
    ) -> VerificationResult | Awaitable[VerificationResult]:
        ...


class ResearchFinalizer(Protocol):
    def finalize(self, state: ResearchState) -> str:
        ...


class ResearchNodes:
    """Five thin business-control nodes over pluggable Harness executors."""

    def __init__(
        self,
        *,
        executor: ResearchActionExecutor,
        planner: ResearchPlanner,
        action_selector: ResearchActionSelector,
        verifier: ResearchVerifier,
        finalizer: ResearchFinalizer,
    ) -> None:
        self._executor = executor
        self._planner = planner
        self._action_selector = action_selector
        self._verifier = verifier
        self._finalizer = finalizer

    def normalize(self, state: ResearchState) -> dict:
        query = state["query"].strip()
        if not query:
            raise ValueError("query must not be empty")

        return {
            "normalized_task": " ".join(query.split()),
            "current_step": state.get("current_step", 0),
            "step_count": state.get("step_count", 0),
            "max_steps": state.get("max_steps", 4),
            "retry_count": state.get("retry_count", 0),
            "max_retries": state.get("max_retries", 1),
            "last_action": state.get("last_action"),
            "termination_reason": None,
            "incomplete": False,
            "final_answer": None,
        }

    async def plan(self, state: ResearchState) -> dict:
        normalized_task = state["normalized_task"]
        plan_or_awaitable = self._planner.plan(normalized_task)
        plan = (
            await plan_or_awaitable
            if inspect.isawaitable(plan_or_awaitable)
            else plan_or_awaitable
        )

        if not plan:
            raise ValueError("research plan must contain at least one step")

        return {
            "plan": plan,
            "current_step": 0,
        }

    async def act(
        self,
        state: ResearchState,
        runtime: Runtime[ResearchContext],
    ) -> dict:
        action_or_awaitable = self._action_selector.select_action(state)
        action = (
            await action_or_awaitable
            if inspect.isawaitable(action_or_awaitable)
            else action_or_awaitable
        )

        if action is None:
            return {
                "termination_reason": TerminationReason.NO_ACTIONABLE_PATH,
            }

        trace_metadata: dict[str, object] = {
            "decision_reason": action.reason,
            "research_step": state.get("current_step", 0),
            "research_action": action.tool_name,
            "research_action_arguments": action.arguments,
        }
        if runtime.context:
            trace_id = runtime.context.get("trace_id")
            if trace_id:
                trace_metadata["trace_id"] = trace_id

        updates = await self._executor.execute(
            action=action,
            state=state,
            trace_metadata=trace_metadata,
        )
        return {
            **updates,
            "last_action": action,
        }

    async def verify(self, state: ResearchState) -> dict:
        result_or_awaitable = self._verifier.verify(state)
        result = (
            await result_or_awaitable
            if inspect.isawaitable(result_or_awaitable)
            else result_or_awaitable
        )
        return {"verification": result}

    def finalize(self, state: ResearchState) -> dict:
        reason = determine_termination_reason(state)
        final_state = dict(state)
        final_state["termination_reason"] = reason
        final_state["incomplete"] = reason is not TerminationReason.COMPLETED

        answer = self._finalizer.finalize(final_state)

        return {
            "termination_reason": reason,
            "incomplete": reason is not TerminationReason.COMPLETED,
            "final_answer": answer,
        }


def determine_termination_reason(
    state: ResearchState,
) -> TerminationReason:
    verification = state.get("verification")
    if verification is not None and verification.sufficient:
        return TerminationReason.COMPLETED

    preset = state.get("termination_reason")
    if preset is not None:
        return preset

    if state.get("step_count", 0) >= state.get("max_steps", 4):
        return TerminationReason.MAX_STEPS

    last_result: ToolResult | None = state.get("last_tool_result")
    if (
        last_result is not None
        and not last_result.ok
        and last_result.error_code in PERMANENT_TOOL_ERRORS
    ):
        return TerminationReason.PERMANENT_FAILURE

    if state.get("retry_count", 0) > state.get("max_retries", 1):
        return TerminationReason.RETRY_EXHAUSTED

    return TerminationReason.NO_ACTIONABLE_PATH


def should_finalize(state: ResearchState) -> bool:
    verification = state.get("verification")
    if verification is not None and verification.sufficient:
        return True

    if state.get("termination_reason") is not None:
        return True

    if state.get("step_count", 0) >= state.get("max_steps", 4):
        return True

    last_result = state.get("last_tool_result")
    if (
        last_result is not None
        and not last_result.ok
        and last_result.error_code in PERMANENT_TOOL_ERRORS
    ):
        return True

    return state.get("retry_count", 0) > state.get("max_retries", 1)
